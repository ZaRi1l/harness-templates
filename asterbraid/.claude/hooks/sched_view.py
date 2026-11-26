"""sched 보기: 지표·이상 감지·Mod가 읽는 스냅샷. 상태를 읽기만 한다."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sched_store import *  # noqa: E402,F401,F403
from sched_rules import *  # noqa: E402,F401,F403


def metrics(st: dict, cfg: dict) -> dict:
    tasks = list(st["tasks"].values())
    impl_limit = limit(st, cfg, "implement")
    per_state, lead, spans = {}, [], []
    for t in tasks:
        marks = [(datetime.fromisoformat(t["created"]), "PENDING")] + \
                [(datetime.fromisoformat(h["at"]), h["to"]) for h in t["history"]]
        marks.append((datetime.now().astimezone(), None))
        for (start, state), (end, _) in zip(marks, marks[1:]):
            if state in ("COMPLETED", "CANCELLED"):
                break
            per_state[state] = per_state.get(state, 0.0) + (end - start).total_seconds()
            if state == "IMPLEMENTING":
                spans.append((start, end))
        if t["completed"]:
            lead.append((datetime.fromisoformat(t["completed"]) - datetime.fromisoformat(t["created"])).total_seconds())
    points = sorted([(s, 1) for s, _ in spans] + [(e, -1) for _, e in spans])
    peak = running = 0
    for _, delta in points:
        running += delta
        peak = max(peak, running)
    wall = (max(e for _, e in spans) - min(s for s, _ in spans)).total_seconds() if spans else 0
    kinds = [h.get("kind") for t in tasks for h in t["history"]]
    done = sum(1 for t in tasks if t["state"] == "COMPLETED")
    evid = [e for t in tasks for e in t["evidence"]]
    return {
        "tasks": len(tasks), "completed": done,
        "completion_rate": round(done / len(tasks), 2) if tasks else None,
        "seconds_by_state": {k: round(v) for k, v in sorted(per_state.items())},
        "avg_lead_time_s": round(sum(lead) / len(lead)) if lead else None,
        "implement_peak_parallel": peak, "implement_slot_limit": impl_limit,
        "implement_slot_utilization": round(sum((e - s).total_seconds() for s, e in spans) / (wall * impl_limit), 2)
        if wall else None,
        "retries_total": sum(t["retries"] for t in tasks),
        "verify_fail_rate": round(sum(1 for e in evid if e["verdict"] == "FAIL") / len(evid), 2) if evid else None,
        "merge_conflicts": kinds.count("conflict"), "integration_test_failures": kinds.count("integration_test"),
        "queue": {s: sum(1 for t in tasks if t["state"] == s) for s in ("AWAITING_APPROVAL", "READY", "VERIFYING", "READY_TO_MERGE")},
        "tokens": "unavailable (Claude Code가 Hook·스크립트에 토큰 사용량을 주지 않는다. /cost로 확인)",
        "slot_limit_note": "활용률은 지금의 슬롯 한도로 계산한다. 실행 중에 한도를 바꿨으면 과거 구간은 정확하지 않다",
    }


# ---------- 스냅샷 (Mod·대시보드가 읽는 읽기 전용 계약) ----------

def timeline(t: dict) -> list:
    marks = [(t["created"], "PENDING")] + [(h["at"], h["to"]) for h in t["history"]]
    return [{"state": s, "start": a, "end": b} for (a, s), (b, _) in zip(marks, marks[1:] + [(None, None)])]


LONG_STAGE = {"factor": 3, "min_s": 600, "samples": 3}  # 중앙값의 3배를 넘고 10분 이상, 과거 표본 3개 이상
WATCHED = ("RESEARCHING", "DESIGNING", "PREVIEWING", "IMPLEMENTING", "VERIFYING", "MERGING")


def anomalies(st: dict) -> list:
    """숫자로만 정하는 경고. 원인 판단은 manager가 한다. 과거 기록이 모자라면 경고하지 않는다."""
    now_dt = datetime.now().astimezone()
    done, current = {}, []
    for t in st["tasks"].values():
        marks = [(t["created"], "PENDING")] + [(h["at"], h["to"]) for h in t["history"]]
        for (a, s), (b, _) in zip(marks, marks[1:]):
            if s in WATCHED:
                done.setdefault(s, []).append((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds())
        at, s = marks[-1]
        if s in WATCHED:
            current.append((t["id"], s, (now_dt - datetime.fromisoformat(at)).total_seconds()))
    out = []
    for tid, s, secs in current:
        past = sorted(done.get(s, []))
        if len(past) < LONG_STAGE["samples"]:
            continue
        median = past[len(past) // 2]
        if secs >= LONG_STAGE["min_s"] and secs > LONG_STAGE["factor"] * median:
            out.append({"kind": "long-stage", "task": tid, "state": s, "seconds": round(secs), "median_s": round(median),
                        "text": f"{tid} {s} {round(secs / 60)}분째 (과거 중앙값 {round(median / 60, 1)}분)"})
    return out


def read_jsonl_tail(path: Path, n: int) -> list:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]:
        with contextlib.suppress(ValueError):
            out.append(json.loads(line))
    return out


def snapshot(root: Path, cfg: dict, st: dict) -> dict:
    """같은 잠금 안에서 계산한 읽기 전용 스냅샷. 필드를 바꾸면 SNAPSHOT_SCHEMA를 올린다."""
    integ = st["integration_branch"]
    head = branch_head(root, integ) if integ else None
    place = {}  # 작업 ID → (풀, 바로 시작 가능, 기다리는 이유)
    for pool in ("research", "design", "review", "preview", "implement", "merge"):
        picks, waiting = dispatchable(st, cfg, pool)
        for t in picks:
            place[t["id"]] = (pool, True, None)
        for w in waiting:
            place[w["id"]] = (pool, False, w["why"])
    tasks, counts, events = [], {}, []
    for t in sorted(st["tasks"].values(), key=lambda t: (-t["priority"], t["created"], t["id"])):
        counts[t["state"]] = counts.get(t["state"], 0) + 1
        pool, runnable, why = place.get(t["id"], (None, False, None))
        if t["state"] in ("READY", "RETRY", "PENDING") and why is None and not runnable:
            waits = waiting_deps(st, t)
            why = f"선행 작업 미완료: {waits}" if waits else None
        verifying = t["state"] in ("VERIFYING", "READY_TO_MERGE")
        tasks.append({
            **{k: t.get(k) for k in ("id", "title", "state", "priority", "risk", "profile", "paths", "forbid", "deps",
                                     "criteria", "agent", "attempts", "branch", "worktree", "base", "head", "retries",
                                     "reason", "notes", "created", "updated", "completed", "cleaned", "design_depth",
                                     "design_review")},
            "review_pending": review_pending(t), "verifiers_assigned": assigned_verifiers(t),
            "pool": pool, "runnable": runnable, "waiting": why, "decisions_open": open_decisions(st, t),
            "started": next((h["at"] for h in t["history"] if h.get("pool")), None),  # 첫 배정(걸린 시간 계산용)
            "evidence": {"valid": len(valid_evidence(t)) if verifying else None,
                         "required": required_verifiers(cfg, t),
                         "items": [{k: e.get(k) for k in ("at", "verifier", "verdict", "sha", "scope", "notes")}
                                   for e in t["evidence"][-6:]]},
            "preview": t["preview"], "timeline": timeline(t)[-30:]})
        events += [{"task": t["id"], **{k: h.get(k) for k in ("at", "from", "to", "reason", "kind", "agent", "pool",
                                                              "attempt")}} for h in t["history"][-30:]]
    d = state_dir(root)
    return {
        "schema": "autonomous-dashboard", "schema_version": SNAPSHOT_SCHEMA,
        "generated_at": now(), "generated_ms": int(time.time() * 1000),
        "project": {"root": str(root), "name": root.name, "state_dir": str(d)},
        "integration": {"branch": integ, "head": head},
        "paused": st.get("paused"), "away": st.get("away"), "away_log": st.get("away_log", [])[-30:],
        "design_review_mode": st.get("design_review") or cfg["design_review"],
        "total": len(tasks), "completed": counts.get("COMPLETED", 0), "counts": counts,
        "slots": {**{p: {"used": used(st, p), "limit": limit(st, cfg, p)} for p in SLOT_STATE},
                  "verify": {"queued": counts.get("VERIFYING", 0), "limit": limit(st, cfg, "verify")}},
        "tasks": tasks,
        "decisions": sorted(st["decisions"].values(), key=lambda x: x["created"]),
        "notes": st["notes_log"][-30:],
        "events": sorted(events, key=lambda x: x["at"] or "")[-80:],
        "integration_log": read_jsonl_tail(d / "integration-log.jsonl", 20),
        "metrics": metrics(st, cfg),
        "anomalies": anomalies(st),
        "dashboard_html": str(preview_dir(root) / "index.html"),
    }
