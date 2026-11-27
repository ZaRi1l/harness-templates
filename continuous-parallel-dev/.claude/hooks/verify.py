#!/usr/bin/env python3
"""Claude Code Stop / SubagentStop verification hook (standard library only, Python 3.8+).

Project-specific values (test command, relevant paths, preflight checks) live in
verify.config.json next to this file. Override the location with $VERIFY_CONFIG.

Status and exit codes:
  PASS     exit 0  tests ran and passed (scope "full" = whole test command, "scoped" = --only run)
  SKIP     exit 0  no relevant change, or this exact tree already passed a full run
  FAIL     exit 2  tests failed / timed out -> Claude keeps working and gets the stderr report
  BLOCKED  exit 2  environment not ready, config invalid or target invalid; a test result would be meaningless
  Loop guard: when Claude is already continuing because of this hook (stop_hook_active) and the
  result is FAIL/BLOCKED again, the exit code is 1 (no further block) but the status stays
  FAIL/BLOCKED in the report. Exit 0 is only ever returned with PASS or SKIP.

Every run writes a JSON report (latest) and appends to a JSONL history, both inside the verified
tree's own git dir (per worktree):
  git -C <worktree> rev-parse --path-format=absolute --git-path claude-verify-report.json
  git -C <worktree> rev-parse --path-format=absolute --git-path claude-verify-history.jsonl

Target selection (verifies the tree the agent actually worked in, not the main checkout):
  --target DIR  >  $VERIFY_TARGET_DIR  >  last cwd recorded in the subagent transcript
  (when it differs from the hook cwd)  >  hook input "cwd"  >  current directory

Environment knobs:
  VERIFY_CONFIG     path of the JSON config (default: verify.config.json next to this file)
  VERIFY_PYTHON     interpreter substituted for {python} (default: first python_candidates hit in the
                    main checkout, else this interpreter)
  VERIFY_BASE_REF   ref whose merge-base defines "committed changes" (default: config base_refs)
  VERIFY_TIMEOUT    seconds for the test run (default: config timeout_s; keep below the hook timeout)
  VERIFY_GIT        git executable (default: PATH, then the usual Git for Windows locations)
"""

from __future__ import annotations

import argparse
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

CONFIG_PATH = Path(os.environ.get("VERIFY_CONFIG") or Path(__file__).with_name("verify.config.json"))
DEFAULTS = {
    "test_command": None,
    "test_env": {},
    "python_candidates": [".venv/Scripts/python.exe", ".venv/bin/python"],
    "relevant_prefixes": [],
    "relevant_suffixes": [],
    "relevant_files": [],
    "ignored_prefixes": [".claude/"],
    "ignored_suffixes": [".md"],
    "base_refs": ["main", "master", "origin/HEAD"],
    "timeout_s": 240,
    "success_pattern": None,
    "blocked_patterns": [],
    "preflight": [],
}

MAX_REPORT_CHARS = 6000
STATE_NAME = "claude-verify-state.json"
LOG_NAME = "claude-verify-last.log"
REPORT_NAME = "claude-verify-report.json"
HISTORY_NAME = "claude-verify-history.jsonl"

CFG: dict = {}
REPORT: dict = {}
PATHS: dict = {}
T0 = time.time()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def log(msg: str) -> None:
    print(f"[verify] {msg}", file=sys.stderr)


def finish(status: str, code: int, message: str) -> int:
    if status not in ("PASS", "SKIP") and code == 0:
        code = 2  # a non-passing result must never exit 0
    REPORT.update(
        status=status,
        exit_code=code,
        loop_guard_applied=code == 1,
        reason=None if status == "PASS" else message,
        summary=message if status == "PASS" else None,
        finished_at=now_iso(),
        duration_s=round(time.time() - T0, 1),
    )
    print(f"[verify] {status}: {message}", file=sys.stderr)
    report_path = PATHS.get("report")
    if report_path:
        try:
            report_path.write_text(json.dumps(REPORT, indent=2, ensure_ascii=False), encoding="utf-8")
            with PATHS["history"].open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(REPORT, ensure_ascii=False) + "\n")
            print(f"[verify] report: {report_path}", file=sys.stderr)
        except OSError as e:
            log(f"warning: could not write report {report_path}: {e}")
    else:
        log("warning: no git dir available; JSON report not written")
    return code


def find_git() -> str:
    """Hooks run without a shell, so Git for Windows may be missing from PATH."""
    found = os.environ.get("VERIFY_GIT") or shutil.which("git")
    if found:
        return found
    for candidate in (r"C:\Program Files\Git\cmd\git.exe", r"C:\Program Files (x86)\Git\cmd\git.exe"):
        if Path(candidate).is_file():
            return candidate
    return "git"


GIT = find_git()


def git(cwd: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        [GIT, "-C", str(cwd), *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def git_path(root: Path, name: str) -> Path:
    return Path(git(root, "rev-parse", "--path-format=absolute", "--git-path", name).strip())


def set_report_paths(root: Path) -> None:
    PATHS.update(
        report=git_path(root, REPORT_NAME),
        history=git_path(root, HISTORY_NAME),
        state=git_path(root, STATE_NAME),
        log=git_path(root, LOG_NAME),
    )


def load_config() -> dict:
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config must be a JSON object")
    unknown = set(data) - set(DEFAULTS) - {"_comment"}
    if unknown:
        raise ValueError(f"unknown config keys: {sorted(unknown)}")
    cfg = {**DEFAULTS, **data}
    cmd = cfg["test_command"]
    if not isinstance(cmd, list) or not cmd or not all(isinstance(p, str) for p in cmd):
        raise ValueError("test_command must be a non-empty list of strings")
    for item in cfg["preflight"]:
        if not isinstance(item, dict) or not isinstance(item.get("command"), list) or not item["command"]:
            raise ValueError("each preflight entry needs a non-empty 'command' list")
    return cfg


def same_path(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def read_hook_input() -> dict:
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")  # hook input is UTF-8, not the locale codepage
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log("hook input is not JSON; ignoring it")
        return {}
    return data if isinstance(data, dict) else {}


def transcript_last_cwd(transcript: str | None) -> str | None:
    """Last `cwd` recorded in a subagent transcript (JSONL)."""
    if not transcript:
        return None
    path = Path(os.path.expanduser(transcript))
    if not path.is_file():
        return None
    last = None
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"cwd"' not in line:
                    continue
                try:
                    cwd = json.loads(line).get("cwd")
                except (json.JSONDecodeError, AttributeError):
                    continue
                if isinstance(cwd, str) and cwd:
                    last = cwd
    except OSError:
        return None
    return last


def resolve_target(args: argparse.Namespace, hook: dict) -> tuple[Path, str]:
    if args.target:
        return Path(args.target), "--target"
    if os.environ.get("VERIFY_TARGET_DIR"):
        return Path(os.environ["VERIFY_TARGET_DIR"]), "VERIFY_TARGET_DIR"
    hook_cwd = hook.get("cwd")
    if hook.get("hook_event_name") == "SubagentStop":
        t_cwd = transcript_last_cwd(hook.get("agent_transcript_path"))
        if t_cwd and Path(t_cwd).is_dir() and not (hook_cwd and same_path(t_cwd, hook_cwd)):
            return Path(t_cwd), "subagent transcript cwd"
    if hook_cwd and Path(hook_cwd).is_dir():
        return Path(hook_cwd), "hook input cwd"
    return Path.cwd(), "process cwd"


def is_relevant(path: str) -> bool:
    path = path.replace("\\", "/")
    if path.startswith(tuple(CFG["ignored_prefixes"])) or path.endswith(tuple(CFG["ignored_suffixes"])):
        return False
    return (
        path.startswith(tuple(CFG["relevant_prefixes"]))
        or path in CFG["relevant_files"]
        or path.endswith(tuple(CFG["relevant_suffixes"]))
    )


def uncommitted_changes(root: Path) -> list[tuple[str, str]]:
    items = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").split("\0")
    entries, i = [], 0
    while i < len(items):
        item = items[i]
        i += 1
        if len(item) < 4:
            continue
        status, path = item[:2], item[3:]
        if status[0] in "RC":  # rename/copy: the next field is the source path; moving a file out counts too
            source = items[i] if i < len(items) else ""
            i += 1
            if is_relevant(source) and not is_relevant(path):
                path = source
        if is_relevant(path):
            entries.append((status, path))
    return entries


def resolve_head(root: Path) -> str | None:
    commit = git(root, "rev-parse", "--verify", "--quiet", "HEAD^{commit}", check=False).strip()
    return commit if commit else None


def resolve_branch(root: Path) -> str:
    ref = git(root, "symbolic-ref", "-q", "--short", "HEAD", check=False).strip()
    if ref:
        return ref
    abbrev = git(root, "rev-parse", "--abbrev-ref", "HEAD", check=False).strip()
    return abbrev or "HEAD"


def changed_paths(root: Path, rev_range: str) -> list[str]:
    """Relevant paths changed in rev_range. -z keeps non-ASCII paths unquoted; --no-renames lists both
    sides of a rename so a file moved out of a relevant path is still seen."""
    out = git(root, "diff", "--name-only", "-z", "--no-renames", rev_range, check=False)
    return [p for p in out.split("\0") if p and is_relevant(p)]


def committed_changes(root: Path, last_pass_head: str | None = None, head: str | None = None) -> tuple[str | None, list[str]]:
    if not head:
        return None, []
    base, changed = None, []
    for ref in filter(None, [os.environ.get("VERIFY_BASE_REF"), *CFG["base_refs"]]):
        if git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False).strip():
            base = git(root, "merge-base", ref, "HEAD", check=False).strip()
            if base:
                changed = changed_paths(root, f"{base}..HEAD")
                break
            base = None
    # Work committed on the base branch itself (merge-base == HEAD) is invisible above: also count
    # everything committed since the last full PASS. A vanished commit (rebase, gc) is ignored.
    if last_pass_head and last_pass_head != head:
        if git(root, "rev-parse", "--verify", "--quiet", f"{last_pass_head}^{{commit}}", check=False).strip():
            changed += [p for p in changed_paths(root, f"{last_pass_head}..HEAD") if p not in changed]
    return base, changed


def fingerprint(root: Path, head: str | None, dirty: list[tuple[str, str]], python: str) -> str:
    h = hashlib.sha256()
    h.update(f"{head or ''}\0{python}\0".encode())
    h.update(Path(__file__).read_bytes())  # editing this hook or its config invalidates cached results
    h.update(CONFIG_PATH.read_bytes())
    for status, path in sorted(dirty, key=lambda e: e[1]):
        h.update(f"\0{status}\0{path}\0".encode())
        f = root / path
        if f.is_file():
            h.update(hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest()


def load_state() -> dict:
    try:
        return json.loads(PATHS["state"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(result: str, fp: str, detail: str, head: str | None = None) -> None:
    payload = {"result": result, "fingerprint": fp, "time": now_iso(), "detail": detail,
               "command": REPORT.get("command")}
    # last full PASS commit: set on PASS, carried over unchanged on FAIL (never lost)
    last_pass_head = head if result == "PASS" else load_state().get("last_pass_head")
    if last_pass_head:
        payload["last_pass_head"] = last_pass_head
    try:
        PATHS["state"].write_text(json.dumps(payload), encoding="utf-8")
    except OSError as e:
        log(f"warning: could not write state file: {e}")


def pick_python(main_root: Path) -> str:
    if os.environ.get("VERIFY_PYTHON"):
        return os.environ["VERIFY_PYTHON"]
    for rel in CFG["python_candidates"]:
        if (main_root / rel).is_file():
            return str(main_root / rel)
    return sys.executable


def expand(template: list[str], python: str, root: Path) -> list[str]:
    """Substitute {python}/{root} and resolve the executable (Windows needs npm.cmd etc. resolved)."""
    cmd = [part.replace("{python}", python).replace("{root}", str(root)) for part in template]
    cmd[0] = shutil.which(cmd[0]) or cmd[0]
    return cmd


def executable_exists(name: str) -> bool:
    return Path(name).is_file() or shutil.which(name) is not None


def preflight(python: str, root: Path, env: dict) -> list[str]:
    """Environment problems that would make a test result meaningless (-> BLOCKED)."""
    test_cmd = expand(CFG["test_command"], python, root)
    if not executable_exists(test_cmd[0]):
        return [f"test command executable not found: {test_cmd[0]}"]
    problems = []
    for item in CFG["preflight"]:
        name = item.get("name") or " ".join(item["command"])
        cmd = expand(item["command"], python, root)
        if not executable_exists(cmd[0]):
            problems.append(f"{name}: executable not found: {cmd[0]}")
            continue
        try:
            res = subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=60)
        except subprocess.TimeoutExpired:
            problems.append(f"{name}: timed out after 60s")
            continue
        if res.returncode != 0:
            out = (res.stderr.strip() or res.stdout.strip())[-500:]
            problems.append(f"{name}: exit {res.returncode}: {out}")
    return problems


def tail(output: str) -> str:
    text = output.strip()
    return text if len(text) <= MAX_REPORT_CHARS else "... (truncated)\n" + text[-MAX_REPORT_CHARS:]


def matched_line(output: str, m: re.Match) -> str:
    start = output.rfind("\n", 0, m.start()) + 1
    end = output.find("\n", m.end())
    return output[start:end if end >= 0 else len(output)].strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", help="tree to verify (overrides hook input)")
    ap.add_argument("--only", nargs="+", metavar="ARG",
                    help="scoped run: these args are appended to test_command; reported as PASS with scope "
                         "'scoped', never cached")
    ap.add_argument("--force", action="store_true", help="ignore the cache and the no-change skip")
    args = ap.parse_args()

    # A manual run with --target is not a hook: never wait on an open stdin pipe (background callers hang).
    hook = {} if args.target else read_hook_input()
    loop_active = bool(hook.get("stop_hook_active"))
    fail_code = 1 if loop_active else 2
    REPORT.update(
        status=None, started_at=now_iso(), event=hook.get("hook_event_name", "manual"),
        agent_type=hook.get("agent_type"), agent_id=hook.get("agent_id"), hook_cwd=hook.get("cwd"),
        stop_hook_active=loop_active, config=str(CONFIG_PATH), worktree=None, linked_worktree=None,
        branch=None, commit=None, command=None, scope=None, warnings=[],
    )

    target, source = resolve_target(args, hook)
    REPORT.update(target_requested=str(target), target_source=source)
    try:
        root = Path(git(target, "rev-parse", "--show-toplevel").strip())
    except (RuntimeError, OSError) as e:
        project = os.environ.get("CLAUDE_PROJECT_DIR")
        if project:
            try:
                set_report_paths(Path(project))
            except (RuntimeError, OSError):
                pass
        return finish("BLOCKED", fail_code, f"target {target} ({source}) is not inside a git work tree: {e}")

    set_report_paths(root)
    try:
        CFG.update(load_config())
    except (OSError, ValueError) as e:
        return finish("BLOCKED", fail_code, f"verify config {CONFIG_PATH} is missing or invalid: {e}")

    try:
        git_dir = git(root, "rev-parse", "--absolute-git-dir").strip()
        common_dir = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").strip())
        linked = not same_path(git_dir, common_dir)
        head = resolve_head(root)
        branch = resolve_branch(root)
        REPORT.update(worktree=str(root), linked_worktree=linked, main_checkout=str(common_dir.parent),
                      branch=branch, commit=head)
        log(f"event={REPORT['event']} agent={REPORT['agent_type'] or '-'} target={root} ({source}) "
            f"branch={branch} head={head[:10] if head else '(unborn)'} {'linked-worktree' if linked else 'main-checkout'}")
        if REPORT["agent_type"] == "implementer" and not linked:
            msg = "implementer result is not in a linked worktree; isolation may not have been applied"
            REPORT["warnings"].append(msg)
            log(f"WARNING: {msg}")

        state = load_state()
        dirty = uncommitted_changes(root)
        base, committed = committed_changes(root, state.get("last_pass_head"), head)
        python = pick_python(common_dir.parent)
        fp = fingerprint(root, head, dirty, python)
        REPORT.update(base_commit=base, uncommitted_changes=[p for _, p in dirty],
                      committed_changes=committed, python=python, fingerprint=fp)
    except (RuntimeError, OSError) as e:
        return finish("BLOCKED", fail_code, f"git command failed: {e}")

    if not args.only and not args.force:
        if not dirty and not committed:
            return finish("SKIP", 0, "no relevant changes (see relevant_* in the verify config)")
        if state.get("fingerprint") == fp and state.get("result") == "PASS":
            REPORT.update(command=state.get("command"), scope="full", cached=True)
            return finish("SKIP", 0, f"this exact tree already passed a full run ({state.get('detail', '')})")
        if state.get("fingerprint") == fp and state.get("result") == "FAIL":
            REPORT.update(command=state.get("command"), scope="full", cached=True)
            if loop_active:
                return finish("FAIL", 1, "unchanged since the last failing run; not blocking again (loop guard). "
                                         f"Tests are still failing - see {PATHS['log']}")
            return finish("FAIL", 2, f"unchanged since the last failing run:\n{state.get('detail', '')}")

    changed = sorted({p for _, p in dirty} | set(committed))
    if changed:
        more = f" (+{len(changed) - 15} more)" if len(changed) > 15 else ""
        log(f"relevant changes: {', '.join(changed[:15])}{more}")

    test_env = {k: str(v) for k, v in CFG["test_env"].items()}
    env = {**os.environ, **test_env}
    cmd = [*expand(CFG["test_command"], python, root), *(args.only or [])]
    env_prefix = [f"{k}={v}" for k, v in test_env.items()]
    REPORT.update(command=" ".join([*env_prefix, *cmd]), scope="scoped" if args.only else "full")

    problems = preflight(python, root, env)
    if problems:
        return finish("BLOCKED", fail_code,
                      "test environment not ready - a test result would be meaningless:\n  - "
                      + "\n  - ".join(problems)
                      + "\nDo not install packages or weaken tests to get past this; report BLOCKED.")

    timeout = int(os.environ.get("VERIFY_TIMEOUT") or CFG["timeout_s"])
    log(f"running: {' '.join(cmd)} (cwd={root}, timeout={timeout}s)")
    started = time.time()
    try:
        proc = subprocess.run(cmd, cwd=root, env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as e:
        partial = "".join(x for x in (e.stdout, e.stderr) if isinstance(x, str))
        PATHS["log"].write_text(partial, encoding="utf-8")
        REPORT.update(log_path=str(PATHS["log"]))
        detail = f"test run exceeded {timeout}s (possible hang or regression). Partial log: {PATHS['log']}"
        if not args.only:
            save_state("FAIL", fp, detail)
        return finish("FAIL", fail_code, detail)

    output = proc.stdout + proc.stderr
    PATHS["log"].write_text(output, encoding="utf-8")
    elapsed = f"{time.time() - started:.0f}s"
    REPORT.update(test_returncode=proc.returncode, log_path=str(PATHS["log"]))

    # Environment failures (missing dependency, out of memory) say nothing about the code under test.
    if proc.returncode != 0:
        for pattern in CFG["blocked_patterns"]:
            m = re.search(pattern, output, re.M)
            if m:
                return finish("BLOCKED", fail_code,
                              f"test run hit an environment problem (blocked_patterns: {pattern!r}):\n  "
                              f"{matched_line(output, m)}\nFull log: {PATHS['log']}")

    scope = f"scoped: {' '.join(args.only)}" if args.only else "full test command"
    report = tail(output)
    pattern = CFG["success_pattern"]
    pattern_ok = not pattern or re.search(pattern, output, re.M) is not None
    if proc.returncode == 0 and pattern_ok:
        head_tag = f"head {head[:10]}" if head else "unborn HEAD"
        if not args.only:
            save_state("PASS", fp, f"{scope}, {elapsed}, {head_tag}", head)
        return finish("PASS", 0, f"{scope} in {elapsed}; exit 0\n{report}")

    detail = f"{scope} in {elapsed}, exit {proc.returncode}, target {root} (branch {branch})\n{report}\nFull log: {PATHS['log']}"
    if proc.returncode == 0:
        detail = f"test run exited 0 but success_pattern {pattern!r} was not found; cannot confirm a pass.\n" + detail
    if not args.only:
        save_state("FAIL", fp, detail)
    return finish("FAIL", fail_code, detail)


if __name__ == "__main__":
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # Claude reads hook stderr as UTF-8
    try:
        sys.exit(main())
    except Exception as e:  # an internal error must never look like a pass
        sys.exit(finish("BLOCKED", 2, f"verify.py internal error: {type(e).__name__}: {e}"))
