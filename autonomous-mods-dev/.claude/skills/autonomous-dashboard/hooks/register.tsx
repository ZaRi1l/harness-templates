// autonomous-dashboard: sched.py가 쓴 읽기 전용 스냅샷과 이 세션에서 관측한 에이전트 이벤트를 보여 준다.
// 원칙: 관찰 Hook은 next(e)의 결과를 그대로 돌려준다. 작업 상태는 사용자가 직접 누르거나 입력한 행동만
// sched.py 명령(argv, 셸 없음)으로 바꾼다. 렌더링 중에는 파일·프로세스를 건드리지 않는다.
import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Activity, Run, SnapMeta, Snapshot, Tab, Usage, View } from '../types'
import * as C from './core'

const PANE = 'autonomous'
const TITLE = 'Autonomous'

// 저장한 값의 모양을 바꾸면 shape 태그를 올린다. 예전 모양은 초기값으로 읽힌다.
const snapA = atom({ plugin: 'autonomous-dashboard', key: 'snap' } as const, null as Snapshot | null, { shape: 'snap-1' })
const metaA = atom({ plugin: 'autonomous-dashboard', key: 'meta' } as const, C.EMPTY_META, { shape: 'meta-2' })
const runsA = atom({ plugin: 'autonomous-dashboard', key: 'runs' } as const, [] as Run[], { shape: 'runs-3' })
const activityA = atom({ plugin: 'autonomous-dashboard', key: 'activity' } as const, [] as Activity[], { shape: 'activity-1' })
const viewA = atom({ plugin: 'autonomous-dashboard', key: 'view' } as const, C.DEFAULT_VIEW, { shape: 'view-2' })
const usageA = atom({ plugin: 'autonomous-dashboard', key: 'usage' } as const, { costUsd: null, contextPct: null } as Usage, { shape: 'usage-1' })

const TONE_COLOR: Record<C.Tone, string | undefined> = {
  ok: 'success', warn: 'warning', err: 'error', accent: 'claude', info: 'suggestion', dim: undefined, plain: undefined,
}

// 모듈 변수는 리로드 때 사라진다. 다시 계산할 수 있는 것만 둔다.
let cfg: C.Config = C.parseConfig({})
let sysname = ''
let polling = false
let pollSince = 0 // 읽기가 걸려 끝나지 않으면 이 시각부터 30초 뒤 다시 허용한다
let tick = 0
let timer: { cancel: () => void } | null = null
const pendingTools = new Map<string, C.ToolBatch>() // 도구 호출마다 상태를 쓰지 않고 틱마다 모아 쓴다
const warned = new Set<string>() // 이미 알린 경고. 사라지면 지워 다시 생기면 또 알린다
const pendingActivity: Activity[] = []

const caseless = () => sysname === 'Windows_NT'

const log = (level: Activity['level'], source: Activity['source'], task: string | null, text: string, at: number) =>
  pendingActivity.push({ atMs: at, level, source, task, text: C.redact(C.stripControl(text)) })

async function flush($: EngineInterface) {
  if (pendingTools.size) {
    const batch = new Map(pendingTools)
    pendingTools.clear()
    await update($, runsA, list =>
      C.listOf<Run>(list).map(r => {
        const b = batch.get(r.agentId)
        return b ? C.applyBatch(r, b) : r
      }),
    )
  }
  if (pendingActivity.length) {
    const items = pendingActivity.splice(0)
    await update($, activityA, list => C.pushActivity(C.listOf<Activity>(list), items, cfg.maxEvents))
  }
}

/** git 없이 파일만으로 공통 git 폴더를 찾는다: 위로 올라가며 `.git` 폴더, 또는 worktree의 `.git` 파일 → commondir. */
async function commonDirFromFiles($: EngineInterface, start: string): Promise<string | null> {
  let next: string | null = C.joinPath(start, '.')
  for (let i = 0; next && i < 64; i++) {
    const dir: string = next
    next = C.parentDir(dir)
    const dotgit = `${dir === '/' || dir.endsWith('/') ? dir : `${dir}/`}.git`
    const st = await $.fs.stat(dotgit).catch(() => null)
    if (!st) continue
    if (st.kind === 'dir') return C.joinPath(dotgit, '.')
    const gitdir = C.gitdirOf(await $.fs.read(dotgit).catch(() => ''), dir)
    if (!gitdir) return null
    const common = (await $.fs.read(`${gitdir}/commondir`).catch(() => '')).trim()
    return common ? C.joinPath(gitdir, common) : gitdir
  }
  return null
}

/** 저장소의 공통 git 폴더를 찾는다. sched.py의 main_root()와 같은 규칙이다. git 실행이 안 되면 파일로 찾는다. */
async function discover($: EngineInterface, cwd?: string) {
  const at = cwd ?? (await $.session.cwd().catch(() => '.'))
  let why = ''
  const r = await $.process
    .run(['git', 'rev-parse', '--path-format=absolute', '--git-common-dir'], { cwd: at, timeoutMs: 10_000 })
    .catch(err => {
      why = `git 실행 실패: ${err instanceof Error ? err.message : String(err)}`
      return null
    })
  if (r && r.exitCode !== 0) why = `git rev-parse 실패: ${C.fit(r.stderr.trim(), 120)}`
  const common = (r && r.exitCode === 0 ? r.stdout.trim() : '') || (await commonDirFromFiles($, at)) || ''
  if (!common) {
    const error = `git 저장소를 찾지 못했다(${at}). ${why || '.git이 없다'}. 저장소 루트에서 세션을 연다`
    await update($, metaA, m => ({ ...C.EMPTY_META, ...m, root: null, stateDir: null, error }))
    return
  }
  const c = C.joinPath(common, '.')
  const root = C.parentDir(c) ?? c
  await update($, metaA, m => ({ ...C.EMPTY_META, ...m, root, stateDir: `${c}/claude-auto`, error: null }))
}

/** 스냅샷 파일이 바뀌었을 때만 읽는다. 읽기에 실패하면 이전 스냅샷을 두고 stale로 표시한다(작업 상태로 바꾸지 않는다). */
async function poll($: EngineInterface, force = false) {
  const now = await $.clock.now()
  if (polling && now - pollSince < 30_000) return
  polling = true
  pollSince = now
  try {
    const m = await read($, metaA)
    if (!m.stateDir) return
    await update($, metaA, x => ({ ...x, checkedMs: now })) // 바뀐 것이 없어도 확인했다는 표시(화면의 실시간 표시가 돈다)
    const file = `${m.stateDir}/dashboard.json`
    const [st, errSt] = await Promise.all([
      $.fs.stat(file).catch(() => null),
      $.fs.stat(`${m.stateDir}/dashboard.error.json`).catch(() => null),
    ])
    const had = (await read($, snapA)) !== null
    if (!st) {
      await update($, metaA, x => ({ ...x, readAtMs: now, error: '스냅샷이 없다. sched init 전이거나 /autonomous refresh가 필요하다', stale: had }))
      return
    }
    const errText = errSt && errSt.mtimeMs >= st.mtimeMs ? await $.fs.read(`${m.stateDir}/dashboard.error.json`).catch(() => '') : ''
    if (!force && st.mtimeMs === m.mtimeMs && !errText) return
    const parsed = C.parseSnapshot(await $.fs.read(file))
    if ('error' in parsed) {
      if (m.error !== parsed.error) log('error', 'mod', null, parsed.error, now)
      await update($, metaA, x => ({ ...x, readAtMs: now, mtimeMs: st.mtimeMs, error: parsed.error, stale: had }))
      return
    }
    const before = await read($, snapA)
    await update($, snapA, () => parsed.snap)
    const staleErr = errText ? `스냅샷 갱신 실패: ${C.fit(errText, 120)}` : null
    await update($, metaA, x => ({ ...x, readAtMs: now, mtimeMs: st.mtimeMs, error: staleErr, stale: staleErr !== null }))
    for (const c of C.changes(before, parsed.snap)) {
      log('warn', 'mod', null, c, now)
      if (cfg.notify) $.ui.toast(`autonomous: ${c}`)
    }
    await showStatus($)
  } catch (err) {
    const now = await $.clock.now()
    const text = `스냅샷 읽기 실패: ${err instanceof Error ? err.message : String(err)}`
    log('error', 'mod', null, text, now)
    await update($, metaA, x => ({ ...x, readAtMs: now, error: text, stale: true }))
  } finally {
    polling = false
  }
}

async function showStatus($: EngineInterface) {
  if (!cfg.statusLine) return
  const [snap, meta, runs, now] = await Promise.all([read($, snapA), read($, metaA), read($, runsA), $.clock.now()])
  $.ui.status(C.statusText(snap, meta, runs, C.anomalies(snap, runs, now, cfg.stallMs)))
}

/** 새로 생긴 경고만 알린다. tellManager를 켰을 때만 manager가 읽는 메모로 덧붙인다(기본은 화면·토스트만). */
async function checkAnomalies($: EngineInterface) {
  const [snap, runs, now] = await Promise.all([read($, snapA), read($, runsA), $.clock.now()])
  const warns = C.anomalies(snap, runs, now, cfg.stallMs)
  const keys = new Set(warns.map(C.anomalyKey))
  for (const k of [...warned]) if (!keys.has(k)) warned.delete(k)
  const fresh = warns.filter(w => !warned.has(C.anomalyKey(w)))
  for (const w of fresh) {
    warned.add(C.anomalyKey(w))
    log('warn', 'mod', w.task, `경고: ${w.text}`, now)
    if (cfg.notify) $.ui.toast(`autonomous ⚠ ${w.text}`)
  }
  if (fresh.length && cfg.tellManager) {
    const text = `[autonomous-dashboard 관측 경고] ${fresh.map(w => w.text).join(' / ')}. 원인을 확인하고 필요하면 sched recover·재배정·사용자 보고를 판단한다.`
    await $.session.append({ message: { type: 'user', content: [{ type: 'text', text }] } }).catch(() => undefined)
  }
}

/** 관측 값(에이전트 토큰·상태, 세션 비용·컨텍스트)을 Claude Code에서 읽는다. 추정하지 않는다. */
async function observe($: EngineInterface) {
  const runs = await read($, runsA)
  if (runs.some(r => r.status === 'running' || r.status === 'idle')) {
    const agents = await $.agent.list().catch(() => null) // 목록을 못 읽으면 바꾸지 않는다(모두 끝난 것으로 오해하지 않게)
    const now = await $.clock.now()
    if (agents) await update($, runsA, list => C.applyAgentList(C.listOf<Run>(list), agents, now))
  }
  const u = await $.session.usage().catch(() => null)
  if (u) await update($, usageA, () => ({ costUsd: u.cost?.usd ?? null, contextPct: u.context.percent ?? null }))
}

/**
 * 갱신 타이머를 켠다. 파일을 고치면 Mod가 다시 불러와지고(리로드) 그때 타이머가 멈춘다.
 * 세션 시작이 다시 오지 않아도 되살아나게 자주 오는 Hook(도구 호출, 에이전트, 명령, 그리기)에서 부른다.
 */
function ensureTimer($: EngineInterface) {
  if (timer) return
  timer = $.clock.every(cfg.refreshMs, () => void onTick($).catch(() => undefined))
  if (!sysname) void $.session.machine().then(m => { sysname = m?.sysname ?? sysname }).catch(() => undefined)
}

async function onTick($: EngineInterface) {
  tick += 1
  await flush($)
  // 시작할 때 저장소가 없었으면(manager가 git init하기 전) 몇 틱마다 다시 찾는다
  if (!(await read($, metaA)).root && tick % 5 === 0) await discover($)
  await poll($)
  if (tick % 3 === 0) {
    await observe($)
    await checkAnomalies($)
    await showStatus($)
  }
}

/** 프로젝트 안의 sched.py만 argv로 실행한다. 사용자 입력은 인수 하나하나로만 들어간다. */
async function runSched($: EngineInterface, args: string[]): Promise<{ ok: boolean; text: string }> {
  const m = await read($, metaA)
  if (!m.root) return { ok: false, text: m.error ?? '프로젝트를 찾지 못했다' }
  const [script, root] = await Promise.all([
    $.fs.stat(`${m.root}/.claude/hooks/sched.py`, { resolve: true }).catch(() => null),
    $.fs.stat(m.root, { resolve: true }).catch(() => null),
  ])
  if (!script?.realPath || !root?.realPath || script.kind !== 'file' || !C.isInside(script.realPath, root.realPath, caseless())) {
    return { ok: false, text: 'sched.py를 프로젝트 안(.claude/hooks/)에서 찾지 못했다' }
  }
  const r = await $.process
    .run([...C.launcherArgv(cfg.launcher), script.realPath, ...args], { cwd: root.realPath, timeoutMs: 60_000 })
    .catch(err => ({ exitCode: -1, stdout: '', stderr: err instanceof Error ? err.message : String(err) }))
  const text = (r.exitCode === 0 ? r.stdout : r.stderr || r.stdout).trim()
  await poll($, true)
  return { ok: r.exitCode === 0, text: C.fit(C.redact(text), 600) }
}

/** 사용자가 고른 상태 변경 하나. 읽기 전용 설정이면 거부한다. */
async function act($: EngineInterface, a: C.Action): Promise<string> {
  if (!cfg.allowActions) return '읽기 전용 설정(allowActions=false)이다. manager에게 말하거나 CLI로 실행한다'
  const built = C.schedArgs(a, await read($, snapA))
  if ('error' in built) return built.error
  const r = await runSched($, built.args)
  const now = await $.clock.now()
  const label = `sched ${built.args.slice(0, 3).join(' ')}`
  log(r.ok ? 'info' : 'error', 'mod', 'task' in a ? a.task : null, `${label}: ${r.ok ? '완료' : C.fit(r.text, 120)}`, now)
  await update($, viewA, v => ({ ...C.normalizeView(v), lastAction: `${r.ok ? '✓' : '✗'} ${label}${r.ok ? '' : `: ${C.fit(r.text, 80)}`}` }))
  await flush($)
  return r.ok ? `${label} 완료` : `${label} 거부: ${r.text}`
}

async function openPreview($: EngineInterface, task: string | null, version: number | null): Promise<string> {
  if (!task) {
    const r = await runSched($, ['dashboard']) // 읽기 전용 HTML을 새로 만든다. 작업 상태는 바꾸지 않는다
    if (!r.ok) return `대시보드를 만들지 못했다: ${r.text}`
  }
  const m = await read($, metaA)
  const target = C.previewTarget(await read($, snapA), task, version)
  if ('error' in target) return target.error
  const [file, dir] = await Promise.all([
    $.fs.stat(target.file, { resolve: true }).catch(() => null),
    $.fs.stat(`${m.stateDir}/previews`, { resolve: true }).catch(() => null),
  ])
  if (!file?.realPath || !dir?.realPath || !C.isInside(file.realPath, dir.realPath, caseless()) || !C.isPreviewFile(file.realPath)) {
    return `등록된 미리보기 폴더 밖의 파일이라 열지 않는다: ${target.file}`
  }
  const candidates = C.openerArgv(sysname, file.realPath)
  if (!candidates) return `이 환경에서는 브라우저를 열 수 없다. 직접 연다: ${file.realPath}`
  let why = ''
  for (const argv of candidates) {
    const r = await $.process.run(argv, { timeoutMs: 15_000 }).catch(err => {
      why = `${argv[0]} 실행 실패: ${err instanceof Error ? err.message : String(err)}`
      return null
    })
    if (!r) continue
    // explorer.exe는 성공해도 1을 돌려준다. 실행조차 못 했을 때만 실패로 본다
    if (r.exitCode !== 0 && sysname !== 'Windows_NT') {
      why = `${argv[0]} 종료 코드 ${r.exitCode}`
      continue
    }
    return `${target.label}을(를) 기본 브라우저로 열었다: ${file.realPath}`
  }
  return `브라우저를 열지 못했다(${why}). 직접 연다: ${file.realPath}`
}

/** 저장소를 다시 찾고 sched.py로 스냅샷을 새로 만든다. 작업 상태는 바꾸지 않는다. */
async function refreshNow($: EngineInterface) {
  await discover($)
  const r = await runSched($, ['snapshot'])
  return r.ok ? '스냅샷을 다시 만들고 읽었다' : `스냅샷을 만들지 못했다: ${r.text}`
}

async function openPane($: EngineInterface) {
  return $.ui.open({ id: PANE, title: TITLE, columns: cfg.paneColumns, rows: 12 })
}

function setView($: EngineInterface, fn: (v: View) => View) {
  return update($, viewA, v => fn(C.normalizeView(v)))
}

export const register: Register = (on, options) => {
  cfg = C.parseConfig(options)

  // ---------------------------------------------------------------- 수명 주기

  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'autonomous',
      description: 'autonomous 관제 Pane과 미리보기: open|close|status|agents|queue|timeline|git|decisions|metrics|errors|preview|refresh|help',
      argumentHint: '[open|close|<탭>|preview [ID [vN]]|refresh|layout L|help]',
      immediate: true,
    })
    sysname = (await $.session.machine().catch(() => null))?.sysname ?? ''
    await discover($, e.cwd)
    await poll($, true)
    const m = await read($, metaA)
    if (!(await read($, snapA)) && m.stateDir && (await $.fs.exists(`${m.stateDir}/state.json`).catch(() => false))) {
      await runSched($, ['snapshot']) // 상태는 있는데 스냅샷만 없을 때. 작업 상태는 바꾸지 않는다
    }
    timer?.cancel()
    timer = null
    ensureTimer($)
    if (cfg.openOnStart && e.isInteractive) void openPane($).catch(() => undefined)
    await showStatus($)
    return next(e)
  })

  on('session.end', async ($, e, next) => {
    timer?.cancel()
    timer = null
    return next(e)
  })

  // ---------------------------------------------------------------- 관찰 (결과를 바꾸지 않는다)

  on('agent.spawn', async ($, e, next) => {
    const started = await next(e)
    try {
      ensureTimer($)
      if (started.deny === undefined && started.agentId) {
        const id = started.agentId
        const now = await $.clock.now()
        const mk = C.parseMarker(e.prompt)
        const runs = await read($, runsA)
        const known = runs.find(r => r.agentId === id)
        const run: Run = known
          ? { ...known, status: 'running', endedMs: null, lastActiveMs: now, failStreak: 0, ...(mk ? { task: mk.task, pool: mk.pool, attempt: mk.attempt } : {}) }
          : {
              agentId: id, role: e.subagentType, description: C.fit(C.redact(C.stripControl(e.description ?? '')), 80),
              task: mk?.task ?? null, pool: mk?.pool ?? null, attempt: mk?.attempt ?? null, status: 'running', startedMs: now,
              endedMs: null, tools: 0, lastTool: '', errors: 0, lastActiveMs: now, failStreak: 0, tokens: null, cwd: e.cwd ?? '',
            }
        await update($, runsA, list => C.upsertRun(C.listOf<Run>(list), run))
        log('info', 'agent', run.task, `${run.role} ${known ? '재개' : '시작'}${mk ? ` (${mk.pool} #${mk.attempt})` : ' · 미연결'}`, now)
        await flush($)
      }
    } catch {
      // 관찰 실패가 에이전트 실행을 막으면 안 된다
    }
    return started
  }).catch(($, e, next) => next(e))

  on('agent.report', async ($, e, next) => {
    const done = await next(e)
    try {
      ensureTimer($)
      const now = await $.clock.now()
      const status: Run['status'] = e.status === 'completed' ? 'completed' : e.status === 'stopped' ? 'stopped' : 'failed'
      let task: string | null = null
      await update($, runsA, list =>
        C.listOf<Run>(list).map(r => {
          if (r.agentId !== e.agentId) return r
          task = r.task
          return { ...r, status, endedMs: now }
        }),
      )
      log(status === 'completed' ? 'info' : 'error', 'agent', task, `${e.subagentType} ${status === 'completed' ? '끝남' : status}`, now)
      await flush($)
    } catch {
      // 보고서 전달은 그대로 둔다
    }
    return done
  }).catch(($, e, next) => next(e))

  on('tool.call', async ($, e, next) => {
    const ran = await next(e)
    try {
      ensureTimer($)
      const failed = ran.deny !== undefined || ran.isError === true
      if (e.agentId) {
        const summary = C.toolSummary(String(e.tool), e as unknown as Record<string, unknown>, cfg.toolDetail)
        pendingTools.set(e.agentId, C.addCall(pendingTools.get(e.agentId), summary, failed, await $.clock.now()))
      }
      if (ran.deny !== undefined) log('warn', 'tool', null, `${String(e.tool)} 거부${e.agentId ? '(서브에이전트)' : ''}: ${C.fit(ran.deny, 100)}`, await $.clock.now())
    } catch {
      // 도구 결과는 그대로 둔다
    }
    return ran
  }).catch(($, e, next) => next(e))

  // ---------------------------------------------------------------- 명령

  on('command.run', { command: 'autonomous' }, async ($, e) => {
    ensureTimer($)
    if (!(await read($, metaA)).stateDir) await discover($) // 리로드 뒤 처음 명령이면 저장소를 다시 찾는다
    const a = C.parseCommand(e.args)
    const say = (text: string) => ({ text, isTextDisplayOnly: true as const })
    switch (a.kind) {
      case 'help':
        return say(C.HELP.join('\n'))
      case 'error':
        return say(a.message)
      case 'open': {
        const r = await openPane($)
        return say(r.isPlaced ? '관제 Pane을 열었다' : `Pane이 아직 자리를 잡지 못했다: ${r.reason ?? '터미널 폭 부족'}`)
      }
      case 'close':
        await $.ui.close({ id: PANE })
        return say('관제 Pane을 닫았다. 개발 작업은 계속된다')
      case 'tab':
        await setView($, v => ({ ...v, tab: a.tab, ...(a.mode ? { taskMode: a.mode } : {}) }))
        await openPane($)
        return say(`${C.TAB_LABEL[a.tab]} 탭`)
      case 'layout':
        await setView($, v => ({ ...v, layout: a.layout }))
        return say(`레이아웃: ${a.layout}`)
      case 'refresh':
        return say(await refreshNow($))
      case 'preview':
        return say(await openPreview($, a.task, a.version))
      default:
        return say(await act($, a))
    }
  })

  // ---------------------------------------------------------------- 그리기 (읽기만 한다)

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    ensureTimer($) // 그리기는 상태를 쓰지 않는다. 타이머만 켠다(이미 있으면 아무것도 안 한다)
    const els = $.ui.resolve(e)
    const { Box, Text, Button } = els
    const Input = 'Input' in els ? els.Input : null
    const [snap, meta, runs, activity, view0, usage, now] = await Promise.all([
      read($, snapA), read($, metaA), read($, runsA), read($, activityA), read($, viewA), read($, usageA), $.clock.now(),
    ])
    const view = C.normalizeView(view0)
    const W = Math.max(24, e.props.bodyColumns)
    const layout = C.pickLayout(view.layout ?? cfg.layout, e.props.placement, W)

    const seg = (s: C.Seg) => (
      <Text color={TONE_COLOR[s.tone ?? 'plain']} dimColor={s.tone === 'dim'} bold={s.bold}>
        {s.text}
      </Text>
    )
    const line = (l: C.Line) => <Text wrap="truncate">{l.map(seg)}</Text>
    const lines = (ls: C.Line[]) => <Box flexDirection="column">{ls.map(line)}</Box>
    const can = cfg.allowActions

    if (!snap) {
      return (
        <Box flexDirection="column" width={W}>
          <Text bold color="claude">AUTONOMOUS</Text>
          <Text color={meta.error ? 'warning' : undefined} wrap="wrap">{meta.error ?? '스냅샷을 읽는 중'}</Text>
          <Text dimColor wrap="wrap">manager가 `sched init` 뒤 작업을 등록하면 여기에 보인다. /autonomous refresh</Text>
        </Box>
      )
    }

    const warns = C.anomalies(snap, runs, now, cfg.stallMs)
    if (layout === 'mini') return <Box flexDirection="column" width={W}>{lines(C.miniLines(snap, meta, runs, W, warns))}</Box>

    const say = (text: string) => setView($, v => ({ ...v, lastAction: `ℹ ${C.fit(text, 400)}` }))
    const userWait = C.awaiting(snap).length + C.openDecisions(snap).length
    const tabs = (
      <Box flexWrap="wrap" columnGap={1}>
        {C.TABS.map((t, i) => {
          const active = view.tab === t
          const badge = t === 'decisions' && userWait ? ` ${userWait}` : ''
          return (
            <Button key={`tab-${t}`} plain hotkey={String(i + 1)} onPress={() => setView($, v => ({ ...v, tab: t as Tab }))}>
              <Text bold={active} inverse={active} color={active ? 'claude' : badge ? 'warning' : undefined} dimColor={!active && !badge}>{` ${C.TAB_LABEL[t]}${badge} `}</Text>
            </Button>
          )
        })}
      </Box>
    )

    const LAYOUT_LABEL: Record<string, string> = { auto: '자동', compact: '좁게', wide: '넓게', mini: '요약' }
    const nextLayout = (l: string) => (l === 'auto' ? 'compact' : l === 'compact' ? 'wide' : 'auto') as 'auto' | 'compact' | 'wide'

    const commands = () => {
      const curLayout = view.layout ?? cfg.layout
      const open = snap.tasks.filter(t => t.state !== 'COMPLETED' && t.state !== 'CANCELLED').sort((x, y) => y.priority - x.priority).slice(0, 8)
      return (
        <Box flexDirection="column">
          {line(C.section('화면', W))}
          <Box flexWrap="wrap" columnGap={1}>
            <Button key="cmd-refresh" label="새로고침" onPress={() => refreshNow($).then(say)} />
            <Button key="cmd-dashboard" label="브라우저 대시보드" onPress={() => openPreview($, null, null).then(say)} />
            <Button key="cmd-layout" label={`배치: ${LAYOUT_LABEL[curLayout] ?? curLayout}`} onPress={() => setView($, v => ({ ...v, layout: nextLayout(curLayout) }))} />
            <Button key="cmd-close" label="Pane 닫기" onPress={() => $.ui.close({ id: PANE })} />
          </Box>
          {line(C.section('사용자 차례', W))}
          <Box columnGap={1}>
            <Button key="cmd-decisions" label={`승인·결정 열기 (${userWait}건)`} variant={userWait ? 'primary' : undefined} onPress={() => setView($, v => ({ ...v, tab: 'decisions' }))} />
            <Text dimColor>미리보기 승인·수정 요청과 결정은 그 탭의 버튼으로 한다</Text>
          </Box>
          {can && line(C.section('진행', W))}
          {can && (
            <Box columnGap={1}>
              {snap.paused ? (
                <Button key="cmd-resume" label="배정 재개" variant="primary" onPress={() => act($, { kind: 'resume' })} />
              ) : (
                <Button key="cmd-pause" label="새 배정 일시 중지" onPress={() => act($, { kind: 'pause', reason: 'TUI에서 일시 중지' })} />
              )}
              <Text dimColor>{snap.paused ? `중지됨: ${snap.paused.reason}` : '실행 중인 에이전트는 계속된다'}</Text>
            </Box>
          )}
          {can && line(C.section('동시 실행 한도', W))}
          {can && (
            <Box flexWrap="wrap" columnGap={3}>
              {[...C.POOLS, 'verify'].map(pool => {
                const sl = snap.slots[pool]
                const lim = sl?.limit ?? 1
                return (
                  <Box key={`slot-${pool}`} columnGap={1}>
                    <Text>{`${C.POOL_LABEL[pool]} ${pool === 'verify' ? `대기 ${sl?.queued ?? 0}` : `${sl?.used ?? 0}`}/${sl?.limit ?? '?'}`}</Text>
                    <Button key={`dec-${pool}`} label="−" plain onPress={() => act($, { kind: 'limit', pool, n: Math.max(1, lim - 1) })} />
                    <Button key={`inc-${pool}`} label="+" plain onPress={() => act($, { kind: 'limit', pool, n: Math.min(16, lim + 1) })} />
                  </Box>
                )
              })}
            </Box>
          )}
          {can && open.length > 0 && line(C.section('우선순위 (높을수록 먼저 배정)', W))}
          {can &&
            open.map(t => (
              <Box key={`pri-${t.id}`} columnGap={1}>
                <Button key={`pri-up-${t.id}`} label="▲" plain onPress={() => act($, { kind: 'priority', task: t.id, n: t.priority + 1 })} />
                <Button key={`pri-down-${t.id}`} label="▼" plain onPress={() => act($, { kind: 'priority', task: t.id, n: t.priority - 1 })} />
                {line([{ text: `${C.pad(`p${t.priority}`, 4)} `, tone: 'accent' }, { text: `${C.pad(t.id, 8)} `, bold: true }, { text: C.fit(t.title, Math.max(8, W - 22)), tone: 'dim' }])}
              </Box>
            ))}
          {!can && <Text dimColor>읽기 전용 설정(allowActions=false)이라 상태를 바꾸는 버튼은 숨겼다</Text>}
          {line(C.section('같은 일을 하는 명령', W))}
          {C.HELP.map((text, i) => (
            <Text key={`help-${i}`} dimColor wrap="truncate">{text}</Text>
          ))}
        </Box>
      )
    }

    const decisions = () => {
      const waiting = C.awaiting(snap)
      const open = C.openDecisions(snap)
      return (
        <Box flexDirection="column">
          <Button key="open-dashboard" label="미리보기 대시보드 열기(브라우저)" onPress={async () => setView($, v => ({ ...v, lastAction: 'ℹ ' }))
            .then(() => openPreview($, null, null))
            .then(text => setView($, v => ({ ...v, lastAction: `ℹ ${C.fit(text, 100)}` })))} />
          <Text bold color="claude">미리보기 검토 {waiting.length}건</Text>
          {waiting.length === 0 && <Text dimColor>검토를 기다리는 미리보기가 없다</Text>}
          {waiting.map(t => {
            const v = t.preview.versions[t.preview.versions.length - 1]
            if (!v) return null
            const fb = t.preview.feedback[t.preview.feedback.length - 1]
            return (
              <Box key={`pv-${t.id}`} flexDirection="column" marginBottom={1}>
                {line([{ text: `? ${t.id} `, tone: 'warn', bold: true }, { text: t.title }, { text: ` · v${v.n} ${v.sha256.slice(0, 10)} · ${v.summary ?? ''}`, tone: 'dim' }])}
                {fb && line([{ text: `  이전 요청(v${fb.on}): ${fb.text}`, tone: 'dim' }])}
                <Box columnGap={1}>
                  <Button key={`open-${t.id}`} label={`열기 v${v.n}`} onPress={() => openPreview($, t.id, v.n).then(text => setView($, x => ({ ...x, lastAction: `ℹ ${C.fit(text, 100)}` })))} />
                  {can && <Button key={`ok-${t.id}`} label={`승인 v${v.n}`} variant="primary" onPress={() => act($, { kind: 'approve', task: t.id, version: v.n })} />}
                </Box>
                {can && Input && (
                  <Input
                    key={`fb-${t.id}`}
                    label={`v${v.n} 수정 요청`}
                    placeholder="무엇을 바꿀지 쓰고 Enter"
                    onSubmit={text => (text.trim() ? act($, { kind: 'revise', task: t.id, version: v.n, feedback: text.trim() }) : undefined)}
                  />
                )}
              </Box>
            )
          })}
          <Text bold color="claude">사용자 결정 {open.length}건</Text>
          {open.length === 0 && <Text dimColor>열린 결정이 없다</Text>}
          {open.map(d => (
            <Box key={`dec-${d.id}`} flexDirection="column" marginBottom={1}>
              {line([{ text: `? ${d.id} `, tone: 'warn', bold: true }, { text: d.title }, { text: ` · ${d.kind} · 작업 ${d.tasks.join(',') || '-'}`, tone: 'dim' }])}
              <Text wrap="wrap">{d.question}</Text>
              {d.options.map(o => (
                <Box key={`opt-${d.id}-${o.key}`} flexDirection="column">
                  {line([{ text: `  ${o.key}${o.key === d.recommend ? ' ★추천' : ''} `, bold: true, tone: o.key === d.recommend ? 'ok' : 'plain' }, { text: o.label }])}
                  {(o.pros || o.cons) && line([{ text: `    + ${o.pros || '-'}  − ${o.cons || '-'}`, tone: 'dim' }])}
                </Box>
              ))}
              {d.why && <Text dimColor wrap="wrap">추천 이유: {d.why}</Text>}
              {can && (
                <Box columnGap={1} flexWrap="wrap">
                  {d.options.map(o => (
                    <Button key={`pick-${d.id}-${o.key}`} label={`${o.key} 선택`} onPress={() => act($, { kind: 'decide', id: d.id, choice: o.key, note: '' })} />
                  ))}
                </Box>
              )}
            </Box>
          ))}
        </Box>
      )
    }

    const tasksTab = () => (
      <Box flexDirection="column">
        <Box columnGap={1}>
          <Button
            key="task-mode"
            label={view.taskMode === 'list' ? '보기: 목록' : '보기: 타임라인'}
            onPress={() => setView($, v => ({ ...v, taskMode: v.taskMode === 'list' ? 'timeline' : 'list' }))}
          />
          {view.taskMode === 'list' && (
            <Button
              key="filter"
              label={`필터: ${C.FILTER_LABEL[view.filter]}`}
              onPress={() => setView($, v => ({ ...v, filter: C.FILTERS[(C.FILTERS.indexOf(v.filter) + 1) % C.FILTERS.length] ?? 'all' }))}
            />
          )}
          <Text dimColor>{view.taskMode === 'list' ? '줄을 누르면 담당 경로·완료 기준을 펼친다' : '기록된 시작·끝 시각으로 그린다'}</Text>
        </Box>
        {view.taskMode === 'timeline'
          ? lines(C.timelineLines(snap, now, W))
          : C.queueLines(snap, view.filter, view.expanded, W).map((r, i) =>
              r.id ? (
                <Box key={`qr-${r.id}`} flexDirection="column">
                  <Button key={`q-${r.id}`} plain onPress={() => setView($, v => ({ ...v, expanded: v.expanded === r.id ? null : (r.id ?? null) }))}>
                    {r.line.map(seg)}
                  </Button>
                  {can && view.expanded === r.id && snap.tasks.find(t => t.id === r.id)?.state === 'BLOCKED' && (
                    <Box columnGap={1}>
                      <Text>{'   '}</Text>
                      <Button key={`unblock-${r.id}`} label="보류 해제" onPress={() => act($, { kind: 'unblock', task: r.id ?? '' })} />
                      <Text dimColor>원인이 풀렸을 때만 누른다. manager가 다시 배정한다</Text>
                    </Box>
                  )}
                </Box>
              ) : (
                <Box key={`ql-${i}`}>{line(r.line)}</Box>
              ),
            )}
      </Box>
    )

    const errors = () => (
      <Box flexDirection="column">
        <Button key="errors-only" label={view.errorsOnly ? '전체 보기' : '오류·경고만'} onPress={() => setView($, v => ({ ...v, errorsOnly: !v.errorsOnly }))} />
        {lines(C.activityLines(snap, activity, view.errorsOnly, now, W))}
      </Box>
    )

    const body = (tab: Tab, width: number) => {
      switch (tab) {
        case 'overview':
          return lines(C.overviewLines(snap, runs, now, width))
        case 'tasks':
          return tasksTab()
        case 'agents':
          return lines(C.agentLines(snap, runs, now, view.expanded).map(l => C.fitLine(l, width)))
        case 'decisions':
          return decisions()
        case 'git':
          return lines([...C.gitLines(snap, width), ...C.metricLines(snap, runs, usage).map(l => C.fitLine(l, width))])
        case 'activity':
          return errors()
        case 'commands':
          return commands()
      }
    }

    const header = lines(C.headerLines(snap, meta, runs, now, warns, W, cfg.refreshMs))
    const footer = view.lastAction ? <Text dimColor wrap="wrap">{view.lastAction}</Text> : null // 경로를 자르지 않는다

    // 넓은 자리에서는 표 위주 탭 옆에 에이전트 목록을 붙인다. 버튼이 많은 탭은 한 줄로 쓴다
    if (layout === 'wide' && view.tab !== 'commands' && view.tab !== 'decisions') {
      const left = Math.floor(W * 0.62)
      const right = W - left - 2
      return (
        <Box flexDirection="column" width={W}>
          {header}
          {tabs}
          <Box columnGap={2}>
            <Box flexDirection="column" width={left}>{body(view.tab, left)}</Box>
            <Box flexDirection="column" width={right}>
              {line(C.section('에이전트', right))}
              {lines(C.agentLines(snap, runs, now, null).slice(0, 12).map(l => C.fitLine(l, right)))}
            </Box>
          </Box>
          {footer}
        </Box>
      )
    }
    return (
      <Box flexDirection="column" width={W}>
        {header}
        {tabs}
        {body(view.tab, W)}
        {footer}
      </Box>
    )
  }).catch(($, e, next) => {
    // 그리기가 실패해도 빈 Pane을 남기지 않는다. 작업 상태와는 무관하다
    const { Box, Text } = $.ui.resolve(e)
    return (
      <Box flexDirection="column" width={Math.max(20, e.props.bodyColumns)}>
        <Text bold>AUTONOMOUS</Text>
        <Text color="error" wrap="wrap">{`그리기 실패(${next.error.kind}): ${next.error.message ?? ''}. /autonomous refresh 또는 manager에게 상태를 묻는다`}</Text>
      </Box>
    )
  })
}
