// 순수 함수만 둔다: 입력이 같으면 결과가 같고, $·파일·프로세스를 만지지 않는다. 테스트는 이 파일을 직접 검사한다.
import type { Activity, Anomaly, AwayEntry, Decision, Layout, QueueFilter, Run, SnapMeta, SnapTask, Snapshot, Tab, TaskMode, TaskTokens, Usage, View } from '../types'

export const SCHEMA_VERSION = 1
export const TABS: readonly Tab[] = ['overview', 'tasks', 'agents', 'decisions', 'git', 'activity', 'commands']
export const TAB_LABEL: Record<Tab, string> = {
  overview: '현황', tasks: '작업', agents: '에이전트', decisions: '승인·결정', git: '통합·지표', activity: '활동', commands: '명령',
}
export const FILTERS: readonly QueueFilter[] = ['all', 'active', 'waiting', 'blocked', 'done']
export const FILTER_LABEL: Record<QueueFilter, string> = { all: '전체', active: '진행', waiting: '대기', blocked: '보류', done: '완료' }
export const POOLS = ['research', 'design', 'preview', 'implement'] as const
export const POOL_LABEL: Record<string, string> = {
  research: '조사', design: '설계', preview: '미리보기', implement: '구현', verify: '검증', merge: '통합',
}
export const DEFAULT_VIEW: View = { tab: 'overview', layout: null, filter: 'all', taskMode: 'list', expanded: null, errorsOnly: false, showDone: false, showDoneTasks: false, pendingRevise: null, lastAction: null }
export const EMPTY_META: SnapMeta = { root: null, stateDir: null, readAtMs: 0, checkedMs: 0, mtimeMs: 0, error: null, stale: false }

export type Tone = 'ok' | 'warn' | 'err' | 'dim' | 'accent' | 'info' | 'plain'
export type Seg = { text: string; tone?: Tone; bold?: boolean }
export type Line = Seg[]

// ---------------------------------------------------------------- 상태 표기 (색만으로 구분하지 않게 기호와 글자를 함께 쓴다)

export const STATE_MARK: Record<string, { mark: string; label: string; tone: Tone }> = {
  PENDING: { mark: '○', label: '대기', tone: 'dim' },
  RESEARCHING: { mark: '◐', label: '조사', tone: 'info' },
  DESIGNING: { mark: '◐', label: '설계', tone: 'info' },
  PREVIEWING: { mark: '◐', label: '미리보기', tone: 'info' },
  AWAITING_APPROVAL: { mark: '?', label: '승인대기', tone: 'warn' },
  READY: { mark: '·', label: '준비', tone: 'plain' },
  IMPLEMENTING: { mark: '●', label: '구현', tone: 'accent' },
  VERIFYING: { mark: '◎', label: '검증', tone: 'info' },
  READY_TO_MERGE: { mark: '⇢', label: '병합대기', tone: 'info' },
  MERGING: { mark: '⇄', label: '병합중', tone: 'accent' },
  RETRY: { mark: '↻', label: '재작업', tone: 'warn' },
  BLOCKED: { mark: '✗', label: '보류', tone: 'err' },
  COMPLETED: { mark: '✓', label: '완료', tone: 'ok' },
  CANCELLED: { mark: '–', label: '취소', tone: 'dim' },
}
export const mark = (state: string) => STATE_MARK[state] ?? { mark: '?', label: state, tone: 'plain' as Tone }

const ACTIVE = new Set(['RESEARCHING', 'DESIGNING', 'PREVIEWING', 'IMPLEMENTING', 'VERIFYING', 'READY_TO_MERGE', 'MERGING', 'RETRY'])
const WAITING = new Set(['PENDING', 'READY', 'AWAITING_APPROVAL'])
const RUNNING_STATES: Record<string, string> = { RESEARCHING: 'research', DESIGNING: 'design', PREVIEWING: 'preview', IMPLEMENTING: 'implement' }

export const inFilter = (t: SnapTask, f: QueueFilter) =>
  f === 'all' ||
  (f === 'active' && ACTIVE.has(t.state)) ||
  (f === 'waiting' && (WAITING.has(t.state) || t.decisions_open.length > 0)) ||
  (f === 'blocked' && t.state === 'BLOCKED') ||
  (f === 'done' && (t.state === 'COMPLETED' || t.state === 'CANCELLED'))

// ---------------------------------------------------------------- 정규화

export const listOf = <T>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : [])
export const normalizeView = (v: unknown): View => {
  const x = (v && typeof v === 'object' ? v : {}) as Partial<View>
  return {
    tab: TABS.includes(x.tab as Tab) ? (x.tab as Tab) : 'overview',
    layout: (['auto', 'mini', 'compact', 'wide'] as const).includes(x.layout as Layout) ? (x.layout as Layout) : null,
    filter: FILTERS.includes(x.filter as QueueFilter) ? (x.filter as QueueFilter) : 'all',
    taskMode: x.taskMode === 'timeline' ? 'timeline' : 'list',
    expanded: typeof x.expanded === 'string' ? x.expanded : null,
    errorsOnly: x.errorsOnly === true,
    showDone: x.showDone === true,
    showDoneTasks: x.showDoneTasks === true,
    pendingRevise: x.pendingRevise && typeof x.pendingRevise === 'object' && typeof x.pendingRevise.text === 'string' ? x.pendingRevise : null,
    lastAction: typeof x.lastAction === 'string' ? x.lastAction : null,
  }
}

/** 스냅샷 텍스트를 검사한다. 스키마가 다르면 읽지 않는다(필드 뜻이 달라졌을 수 있다). */
export const parseSnapshot = (text: string): { snap: Snapshot } | { error: string } => {
  let data: unknown
  try {
    data = JSON.parse(text)
  } catch (err) {
    return { error: `스냅샷 JSON이 손상됐다: ${err instanceof Error ? err.message : String(err)}` }
  }
  const s = data as Partial<Snapshot> | null
  if (!s || typeof s !== 'object' || s.schema !== 'autonomous-dashboard') return { error: '스냅샷 형식이 아니다' }
  if (s.schema_version !== SCHEMA_VERSION) {
    return { error: `스냅샷 스키마 v${String(s.schema_version)}를 이 Mod(v${SCHEMA_VERSION})가 모른다. sched.py와 Mod를 같은 버전으로 맞춘다` }
  }
  if (!Array.isArray(s.tasks) || !s.project || !s.slots) return { error: '스냅샷 필수 필드가 없다' }
  return { snap: { ...s, decisions: listOf(s.decisions), notes: listOf(s.notes), events: listOf(s.events), integration_log: listOf(s.integration_log), anomalies: listOf(s.anomalies) } as Snapshot }
}

// ---------------------------------------------------------------- 에이전트 ↔ 작업 연결

const MARKER = /^\s*\[auto-task id=([A-Za-z0-9][A-Za-z0-9_.-]{0,63}) pool=(research|design|preview|implement|verify|merge) attempt=(\d{1,6})\]/m

/** 지시문 앞부분의 marker만 믿는다. 역할 이름이 같다는 이유로 작업을 추측해 잇지 않는다. */
export const parseMarker = (prompt: string): { task: string; pool: string; attempt: number } | null => {
  const m = MARKER.exec(prompt.slice(0, 600))
  return m && m[1] && m[2] ? { task: m[1], pool: m[2], attempt: Number(m[3]) } : null
}

/** 상한을 넘으면 끝난 것부터, 그다음 대기, 마지막에 실행 중인 것을 오래된 순으로 버린다. */
export const upsertRun = (runs: Run[], run: Run, max = 60): Run[] => {
  const all = [...runs.filter(r => r.agentId !== run.agentId), run]
  for (const drop of [(r: Run) => r.status !== 'running' && r.status !== 'idle', (r: Run) => r.status === 'idle', () => true]) {
    while (all.length > max) {
      const i = all.findIndex(drop)
      if (i < 0) break
      all.splice(i, 1)
    }
  }
  return all
}

/** 스케줄러 슬롯을 차지한 작업 중 이 세션에서 실행이 관측되지 않은 것. 배정과 실제 실행을 구분해 보여 준다. */
export const unobservedHolders = (snap: Snapshot | null, runs: Run[]) =>
  (snap?.tasks ?? []).filter(
    t => RUNNING_STATES[t.state] && !runs.some(r => r.status === 'running' && r.task === t.id && r.pool === RUNNING_STATES[t.state]),
  )

// ---------------------------------------------------------------- 민감 정보 마스킹

const SECRETS: [RegExp, string][] = [
  [/\bsk-[A-Za-z0-9_-]{12,}/g, 'sk-***'],
  [/\b(gh[pousr]_[A-Za-z0-9]{12,})/g, 'gh*_***'],
  [/\bAKIA[0-9A-Z]{12,}/g, 'AKIA***'],
  [/\b(xox[abpr]-[A-Za-z0-9-]{8,})/g, 'xox*-***'],
  [/(authorization:\s*)(bearer|basic)\s+[^\s"'`]+/gi, '$1$2 ***'],
  [/\b(bearer)\s+[A-Za-z0-9._~+/=-]{12,}/gi, '$1 ***'],
  [/\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)(\s*[=:]\s*)("[^"]*"|'[^']*'|\S+)/gi, '$1$2***'],
  [/-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g, '[private key]'],
]
export const redact = (s: string) => SECRETS.reduce((t, [re, to]) => t.replace(re, to), s)
export const stripControl = (s: string) => s.replace(/[\u0000-\u0008\u000b-\u001f\u007f\u001b]/g, '')

const base = (p: string) => p.split(/[\\/]/).filter(Boolean).pop() ?? p

/** 도구 호출 한 줄 요약. 기본은 이름과 파일 이름만 보이고, masked면 명령 앞부분을 마스킹해 보인다. */
export const toolSummary = (tool: string, input: Record<string, unknown>, detail: 'names' | 'masked') => {
  const file = typeof input.file_path === 'string' ? input.file_path : typeof input.path === 'string' ? input.path : null
  if (file) return `${tool} ${base(file)}`
  if (detail === 'masked' && typeof input.command === 'string') return `${tool} ${fit(redact(stripControl(input.command)).replace(/\s+/g, ' '), 48)}`
  return tool
}

// ---------------------------------------------------------------- 문자 폭 (한글·한자·전각은 2칸)

const WIDE: [number, number][] = [
  [0x1100, 0x115f], [0x2e80, 0x303e], [0x3041, 0x33ff], [0x3400, 0x4dbf], [0x4e00, 0x9fff], [0xa000, 0xa4cf],
  [0xac00, 0xd7a3], [0xf900, 0xfaff], [0xfe30, 0xfe4f], [0xff00, 0xff60], [0xffe0, 0xffe6], [0x1f300, 0x1f64f],
  [0x1f900, 0x1f9ff], [0x20000, 0x3fffd],
]
export const charWidth = (cp: number) => {
  if (cp === 0 || (cp >= 0x300 && cp <= 0x36f) || cp === 0x200b || (cp >= 0xfe00 && cp <= 0xfe0f)) return 0
  return WIDE.some(([a, b]) => cp >= a && cp <= b) ? 2 : 1
}
export const strWidth = (s: string) => [...s].reduce((n, ch) => n + charWidth(ch.codePointAt(0) ?? 0), 0)

/** 칸 폭에 맞게 자른다. 잘랐으면 끝에 …를 붙인다. */
export const fit = (s: string, width: number) => {
  if (width <= 0) return ''
  if (strWidth(s) <= width) return s
  let out = ''
  let used = 0
  for (const ch of s) {
    const w = charWidth(ch.codePointAt(0) ?? 0)
    if (used + w > width - 1) break
    out += ch
    used += w
  }
  return out + '…'
}
export const pad = (s: string, width: number) => {
  const t = fit(s, width)
  return t + ' '.repeat(Math.max(0, width - strWidth(t)))
}
export const lineWidth = (l: Line) => l.reduce((n, s) => n + strWidth(s.text), 0)

/** 줄 전체를 폭에 맞춘다. 앞 조각부터 남기고 넘치는 조각을 자른다. */
export const fitLine = (l: Line, width: number): Line => {
  const out: Line = []
  let room = width
  for (const s of l) {
    if (room <= 0) break
    const w = strWidth(s.text)
    if (w <= room) {
      out.push(s)
      room -= w
    } else {
      out.push({ ...s, text: fit(s.text, room) })
      room = 0
    }
  }
  return out
}

// ---------------------------------------------------------------- 레이아웃

export const pickLayout = (setting: Layout, placement: string, columns: number): 'mini' | 'compact' | 'wide' => {
  if (setting !== 'auto') return setting
  if (placement === 'inline' || placement === 'widget') return 'mini'
  return columns >= 100 ? 'wide' : 'compact'
}

// ---------------------------------------------------------------- 시간

export const ms = (iso: string | null | undefined) => {
  if (!iso) return null
  const t = Date.parse(iso)
  return Number.isNaN(t) ? null : t
}
export const fmtDur = (msec: number) => {
  const s = Math.max(0, Math.round(msec / 1000))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m${String(s % 60).padStart(2, '0')}s`
  return `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}m`
}
export const ago = (msec: number | null, now: number) => (msec === null ? '?' : `${fmtDur(now - msec)} 전`)
export const short = (sha: string | null | undefined, n = 7) => (sha ? sha.slice(0, n) : '-')

// ---------------------------------------------------------------- 명령

export type Action =
  | { kind: 'open' }
  | { kind: 'close' }
  | { kind: 'tab'; tab: Tab; mode: TaskMode | null }
  | { kind: 'refresh' }
  | { kind: 'layout'; layout: Layout }
  | { kind: 'preview'; task: string | null; version: number | null }
  | { kind: 'approve'; task: string; version: number | null }
  | { kind: 'revise'; task: string; version: number | null; feedback: string }
  | { kind: 'decide'; id: string; choice: string; note: string }
  | { kind: 'pause'; reason: string }
  | { kind: 'resume' }
  | { kind: 'limit'; pool: string; n: number }
  | { kind: 'priority'; task: string; n: number }
  | { kind: 'unblock'; task: string }
  | { kind: 'away'; previews: boolean }
  | { kind: 'back' }
  | { kind: 'awayReview'; key: string; redo: boolean }
  | { kind: 'rate'; seconds: number }
  | { kind: 'help' }
  | { kind: 'error'; message: string }

const TAB_WORDS: Record<string, [Tab, TaskMode | null]> = {
  status: ['overview', null], overview: ['overview', null], tasks: ['tasks', null], queue: ['tasks', 'list'],
  timeline: ['tasks', 'timeline'], agents: ['agents', null], decisions: ['decisions', null], git: ['git', null],
  metrics: ['git', null], errors: ['activity', null], activity: ['activity', null], commands: ['commands', null],
}
const version = (w: string | undefined) => {
  if (w === undefined) return null
  const m = /^v?(\d{1,4})$/.exec(w)
  return m ? Number(m[1]) : NaN
}
const ID = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/

export const HELP = [
  '/asterbraid                       관제 Pane 열기 (open과 같다)',
  '/asterbraid close                 Pane만 닫는다. 개발은 계속된다',
  '/asterbraid status|tasks|agents|decisions|git|activity|commands   해당 탭 (queue·timeline·metrics·errors도 된다)',
  '/asterbraid preview [ID [vN]]     브라우저로 미리보기 대시보드 / 작업의 최신·특정 버전 열기',
  '/asterbraid refresh               스냅샷을 다시 만들고 읽는다',
  '/asterbraid layout auto|mini|compact|wide',
  '/asterbraid approve ID vN         사용자가 본 버전 승인 (해시를 함께 기록)',
  '/asterbraid revise ID vN 피드백    수정 요청',
  '/asterbraid decide D-ID KEY [메모] 사용자 결정 기록',
  '/asterbraid pause [사유] | resume  새 배정만 멈춤 / 재개 (실행 중인 에이전트는 계속)',
  '/asterbraid limit POOL N | priority ID N',
  '/asterbraid unblock ID            보류 해제 (sched.py가 재작업·준비 상태로 되돌린다)',
  '/asterbraid away [previews] | back  자리 비움(화면·방향 결정은 추천안, previews면 미리보기도 자동 승인) / 복귀',
  '/asterbraid rate 1|2|5|10         화면 갱신 간격(초)',
]

/** 갱신 간격 단계(초). − + 버튼은 한 단계씩 움직이고 끝에서 멈춘다. */
export const RATES = [1, 2, 5, 10] as const
export const stepRate = (cur: number, dir: 1 | -1): number => {
  const i = RATES.findIndex(r => r >= cur)
  const at = i < 0 ? RATES.length - 1 : i
  return RATES[Math.min(RATES.length - 1, Math.max(0, at + dir))] ?? cur
}

export const parseCommand = (args: string): Action => {
  const words = args.trim().split(/\s+/).filter(Boolean)
  const [head, ...rest] = words
  if (!head || head === 'open') return { kind: 'open' }
  if (head === 'close') return { kind: 'close' }
  if (head === 'help') return { kind: 'help' }
  if (head === 'refresh') return { kind: 'refresh' }
  const tw = TAB_WORDS[head]
  if (tw) return { kind: 'tab', tab: tw[0], mode: tw[1] }
  if (head === 'layout') {
    const l = rest[0] as Layout
    return ['auto', 'mini', 'compact', 'wide'].includes(l) ? { kind: 'layout', layout: l } : { kind: 'error', message: 'layout은 auto|mini|compact|wide다' }
  }
  if (head === 'preview') {
    const [task, v] = rest
    if (task !== undefined && !ID.test(task)) return { kind: 'error', message: `작업 ID 형식이 아니다: ${task}` }
    const n = version(v)
    if (Number.isNaN(n)) return { kind: 'error', message: `버전은 v2처럼 쓴다: ${v}` }
    return { kind: 'preview', task: task ?? null, version: n }
  }
  if (head === 'approve' || head === 'revise') {
    const [task, v, ...fb] = rest
    if (!task || !ID.test(task)) return { kind: 'error', message: `${head} 뒤에 작업 ID를 쓴다` }
    const n = version(v)
    if (n === null || Number.isNaN(n)) return { kind: 'error', message: `${head}는 사용자가 본 버전을 함께 쓴다: /asterbraid ${head} ${task} v2` }
    if (head === 'approve') return { kind: 'approve', task, version: n }
    const feedback = fb.join(' ').trim()
    return feedback ? { kind: 'revise', task, version: n, feedback } : { kind: 'error', message: '수정 요청 내용을 쓴다' }
  }
  if (head === 'decide') {
    const [id, choice, ...note] = rest
    if (!id || !ID.test(id) || !choice) return { kind: 'error', message: '/asterbraid decide D-1 A [메모]' }
    return { kind: 'decide', id, choice, note: note.join(' ') }
  }
  if (head === 'unblock') {
    const [task] = rest
    return task && ID.test(task) ? { kind: 'unblock', task } : { kind: 'error', message: '/asterbraid unblock ID' }
  }
  if (head === 'away') return { kind: 'away', previews: rest[0] === 'previews' || rest[0] === '--previews' }
  if (head === 'back') return { kind: 'back' }
  if (head === 'rate') {
    const k = Number(rest[0])
    return (RATES as readonly number[]).includes(k) ? { kind: 'rate', seconds: k } : { kind: 'error', message: '/asterbraid rate 1|2|5|10' }
  }
  if (head === 'pause') return { kind: 'pause', reason: rest.join(' ') }
  if (head === 'resume') return { kind: 'resume' }
  if (head === 'limit') {
    const [pool, n] = rest
    const k = Number(n)
    return pool && [...POOLS, 'verify'].includes(pool as never) && Number.isInteger(k) && k >= 1 && k <= 16
      ? { kind: 'limit', pool, n: k }
      : { kind: 'error', message: '/asterbraid limit research|design|preview|implement|verify N(1~16)' }
  }
  if (head === 'priority') {
    const [task, n] = rest
    const k = Number(n)
    return task && ID.test(task) && Number.isInteger(k) && Math.abs(k) <= 1000
      ? { kind: 'priority', task, n: k }
      : { kind: 'error', message: '/asterbraid priority ID N' }
  }
  return { kind: 'error', message: `모르는 명령: ${head}. /asterbraid help` }
}

/**
 * 상태를 바꾸는 행동을 sched.py 인수로 바꾼다. 스냅샷에서 대상과 버전·해시를 확인하고, 없는 대상은 거부한다.
 * 실제 규칙(상태 전이, 이미 응답한 결정 등)은 sched.py가 다시 검사한다.
 */
export const schedArgs = (a: Action, snap: Snapshot | null): { args: string[] } | { error: string } => {
  const task = (id: string) => snap?.tasks.find(t => t.id === id)
  switch (a.kind) {
    case 'approve':
    case 'revise': {
      const t = task(a.task)
      if (!t) return { error: `스냅샷에 작업이 없다: ${a.task}` }
      if (t.state !== 'AWAITING_APPROVAL') return { error: `${a.task}는 승인 대기가 아니다(${t.state})` }
      const v = t.preview.versions.find(x => x.n === a.version)
      if (!v) return { error: `${a.task} v${a.version}이 없다. 있는 버전: ${t.preview.versions.map(x => `v${x.n}`).join(', ')}` }
      const latest = t.preview.versions[t.preview.versions.length - 1]
      if (latest && v.n !== latest.n) return { error: `v${v.n}이 아니라 v${latest.n}이 최신이다. v${latest.n}을 보고 다시 판단한다` }
      // 자유 입력은 --옵션=값으로 붙여 보낸다. -로 시작해도 argparse가 옵션으로 읽지 않는다
      return a.kind === 'approve'
        ? { args: ['approve', a.task, '--version', String(v.n), '--sha', v.sha256.slice(0, 16), '--by', 'tui'] }
        : { args: ['revise', a.task, '--version', String(v.n), `--feedback=${a.feedback}`, '--by', 'tui'] }
    }
    case 'decide': {
      const d = snap?.decisions.find(x => x.id === a.id)
      if (!d) return { error: `스냅샷에 결정이 없다: ${a.id}` }
      if (d.state !== 'OPEN') return { error: `${a.id}는 이미 ${d.state}다` }
      if (!d.options.some(o => o.key === a.choice)) return { error: `선택지가 아니다: ${a.choice} (${d.options.map(o => o.key).join(', ')})` }
      return { args: ['decide', 'answer', a.id, `--choice=${a.choice}`, '--by', 'tui', ...(a.note ? [`--note=${a.note}`] : [])] }
    }
    case 'unblock': {
      const t = task(a.task)
      if (!t) return { error: `스냅샷에 작업이 없다: ${a.task}` }
      if (t.state !== 'BLOCKED') return { error: `${a.task}는 보류 상태가 아니다(${t.state})` }
      return { args: ['unblock', a.task, '--reason=TUI에서 보류 해제'] }
    }
    case 'away':
      return { args: ['away', ...(a.previews ? ['--previews'] : []), '--reason=TUI에서 자리 비움'] }
    case 'back':
      return { args: ['back'] }
    case 'awayReview': {
      const e = pendingAway(snap).find(x => x.key === a.key)
      if (!e) return { error: `확인할 자동 결정이 없다: ${a.key}` }
      if (a.redo && e.kind !== 'decision') return { error: '미리보기는 다시 정하기를 지원하지 않는다. manager에게 수정을 요청한다' }
      return { args: ['away-review', a.key, a.redo ? '--redo' : '--keep'] }
    }
    case 'pause':
      return { args: ['pause', `--reason=${a.reason || 'TUI에서 일시 중지'}`] }
    case 'resume':
      return { args: ['resume'] }
    case 'limit':
      return { args: ['limit', a.pool, String(a.n)] }
    case 'priority':
      return task(a.task) ? { args: ['priority', a.task, String(a.n)] } : { error: `스냅샷에 작업이 없다: ${a.task}` }
    default:
      return { error: '상태를 바꾸는 명령이 아니다' }
  }
}

export const launcherArgv = (launcher: string): string[] =>
  launcher === 'python' ? ['python'] : launcher === 'python3' ? ['python3'] : launcher === 'py' ? ['py', '-3'] : ['uv', 'run', '--no-project', '--offline', 'python']

// ---------------------------------------------------------------- 경로와 브라우저

const norm = (p: string) => p.replace(/\\/g, '/').replace(/\/+$/, '')

const isAbsolute = (p: string) => /^([A-Za-z]:)?[\\/]/.test(p)
/** base 기준으로 rel을 잇고 `.`·`..`를 정리한다. rel이 절대 경로면 rel을 쓴다. 구분자는 /. */
export const joinPath = (base: string, rel: string) => {
  const raw = isAbsolute(rel) ? norm(rel) : `${norm(base)}/${rel.replace(/\\/g, '/')}`
  const drive = /^[A-Za-z]:/.exec(raw)?.[0] ?? ''
  const out: string[] = []
  for (const part of raw.slice(drive.length).split('/')) {
    if (part === '' || part === '.') continue
    if (part === '..') out.pop()
    else out.push(part)
  }
  return `${drive}/${out.join('/')}`
}
/** 상위 폴더. 드라이브·파일 시스템 루트면 null. */
export const parentDir = (p: string) => {
  const n = joinPath(p, '.')
  const drive = /^[A-Za-z]:/.exec(n)?.[0] ?? ''
  const rest = n.slice(drive.length)
  if (rest === '/') return null
  const cut = rest.lastIndexOf('/')
  return drive + (cut === 0 ? '/' : rest.slice(0, cut))
}
/** linked worktree의 `.git` 파일(`gitdir: …`)에서 git 폴더를 읽는다. */
export const gitdirOf = (gitFile: string, dir: string) => {
  const m = /^gitdir:\s*(.+?)\s*$/m.exec(gitFile)
  return m?.[1] ? joinPath(dir, m[1]) : null
}
/** 같은 폴더이거나 그 아래인지. Windows는 대소문자를 가리지 않는다. */
export const isInside = (child: string, parent: string, caseless: boolean) => {
  const c = caseless ? norm(child).toLowerCase() : norm(child)
  const p = caseless ? norm(parent).toLowerCase() : norm(parent)
  return c === p || c.startsWith(p + '/')
}

export const isPreviewFile = (file: string) => /\.html?$/i.test(file)

/** 열 미리보기 파일. 작업이 없으면 대시보드, 버전이 없으면 최신 버전. */
export const previewTarget = (snap: Snapshot | null, task: string | null, version: number | null): { file: string; label: string } | { error: string } => {
  if (!snap) return { error: '스케줄러 스냅샷이 없다. /asterbraid refresh' }
  if (!task) return { file: snap.dashboard_html, label: '미리보기 대시보드' }
  const t = snap.tasks.find(x => x.id === task)
  if (!t) return { error: `작업이 없다: ${task}` }
  const vs = t.preview.versions
  if (vs.length === 0) return { error: `${task}에 등록된 미리보기가 없다` }
  const v = version === null ? vs[vs.length - 1] : vs.find(x => x.n === version)
  if (!v) return { error: `${task} v${version}이 없다. 있는 버전: ${vs.map(x => `v${x.n}`).join(', ')}` }
  if (!isPreviewFile(v.file)) return { error: `.html/.htm 미리보기만 연다(OS 기본 프로그램으로 다른 형식을 실행하지 않는다): ${v.file}` }
  return { file: v.file, label: `${task} v${v.n}` }
}

/**
 * 기본 브라우저로 파일을 여는 명령 후보(앞에서부터 시도). 셸을 거치지 않는다.
 * Windows는 설정의 PATH에 C:\\Windows가 빠져 있어도 열리게 절대 경로를 다음 후보로 둔다.
 */
export const openerArgv = (sysname: string, file: string): string[][] | null => {
  if (sysname === 'Windows_NT') {
    const f = file.replace(/\//g, '\\')
    return [['explorer.exe', f], ['C:\\Windows\\explorer.exe', f]]
  }
  if (sysname === 'Darwin') return [['open', file]]
  if (sysname === 'Linux' || sysname.endsWith('BSD')) return [['xdg-open', file]]
  return null
}

// ---------------------------------------------------------------- 요약

export const runningRuns = (runs: Run[]) => runs.filter(r => r.status === 'running')

/** `$.agent.list()`의 한 줄 중 여기서 쓰는 것. */
export type AgentSeen = { readonly id: string; readonly status: string; readonly taskStatus: string; readonly tokenCount?: number }

/**
 * Claude Code가 보고한 에이전트 목록으로 실행 기록을 맞춘다.
 * 팀원은 일을 마쳐도 taskStatus가 running으로 남으니 status(idle)를 본다. 목록에 없는 것은 그대로 둔다(끝은 agent.report로 안다).
 */
export const applyAgentList = (runs: Run[], agents: readonly AgentSeen[], now: number): Run[] => {
  const byId = new Map(agents.map(a => [a.id, a]))
  return runs.map(r => {
    if (r.status !== 'running' && r.status !== 'idle') return r
    const a = byId.get(r.agentId)
    // 실행 중인데 목록에 없으면 그대로 둔다(격리 실행 등은 목록에 안 보일 수 있다. 끝은 agent.report로 안다).
    // 일을 마치고 기다리던 팀원이 목록에서 사라졌으면 끝난 것이다
    if (!a) return r.status === 'idle' ? { ...r, status: 'completed', endedMs: r.endedMs ?? now } : r
    const tokens = typeof a.tokenCount === 'number' ? a.tokenCount : r.tokens
    const end = a.status === 'completed' || a.taskStatus === 'completed' ? 'completed'
      : a.status === 'failed' || a.taskStatus === 'failed' ? 'failed'
      : a.status === 'killed' || a.taskStatus === 'killed' ? 'stopped' : null
    if (end) return { ...r, tokens, status: end, endedMs: now }
    const status: Run['status'] = a.status === 'idle' || a.status === 'waiting' ? 'idle' : 'running'
    return { ...r, tokens, status, endedMs: status === 'idle' ? (r.status === 'idle' ? r.endedMs : now) : null, lastActiveMs: status === 'running' && r.status === 'idle' ? now : r.lastActiveMs }
  })
}
export const slotUsed = (snap: Snapshot) => POOLS.reduce((n, p) => n + (snap.slots[p]?.used ?? 0), 0)
export const openDecisions = (snap: Snapshot | null): Decision[] => (snap?.decisions ?? []).filter(d => d.state === 'OPEN')
export const awaiting = (snap: Snapshot | null) => (snap?.tasks ?? []).filter(t => t.state === 'AWAITING_APPROVAL')
/** 자리 비운 동안 추천안으로 정했고 사용자가 아직 확인하지 않은 것. */
export const pendingAway = (snap: Snapshot | null): AwayEntry[] => (snap?.away_log ?? []).filter(x => x.reviewed === null)

/** 이 실행의 작업에서 같은 단계가 몇 번째인지(스냅샷 이벤트 기준). 모르면 null. */
export const poolRound = (snap: Snapshot | null, r: Run): number | null => {
  if (!snap || !r.task || !r.pool || r.attempt === null) return null
  const n = snap.events.filter(e => e.task === r.task && e.pool === r.pool && (e.attempt ?? 0) <= (r.attempt ?? 0)).length
  return n || null
}

/**
 * 대기(idle) 팀원이 더 쓰일 일이 없는지: 자기 작업이 완료·취소됐고, 그 작업에 기대는 작업도 모두 끝났다.
 * 예: 계약 설계 architect는 계약을 쓰는 구현들이 끝날 때까지 다시 불릴 수 있다.
 */
export const retired = (snap: Snapshot | null, r: Run): boolean => {
  if (r.status !== 'idle' || !snap || !r.task) return false
  const over = (id: string) => ['COMPLETED', 'CANCELLED'].includes(snap.tasks.find(t => t.id === id)?.state ?? '')
  return over(r.task) && snap.tasks.filter(t => t.deps.includes(r.task ?? '')).every(t => over(t.id))
}

/** 작업별 토큰 기록에 지금 실행들을 더한다(같은 에이전트는 최신 값으로). */
export const mergeTaskTokens = (stored: TaskTokens, runs: Run[]): TaskTokens => {
  const out: TaskTokens = {}
  for (const [k, v] of Object.entries(stored ?? {})) out[k] = { ...v }
  for (const r of runs) {
    if (!r.task) continue
    const t = (out[r.task] ??= {})
    t[r.agentId] = r.tokens ?? t[r.agentId] ?? null
  }
  return out
}

export const taskTokenSummary = (tok: TaskTokens, task: string) => {
  const by = Object.values(tok[task] ?? {})
  return by.length ? { tokens: by.reduce<number>((n, x) => n + (x ?? 0), 0), agents: by.length, partial: by.some(x => x === null) } : null
}

/** 완료율은 작업 수 기준이다. 작업 크기가 달라 공수 진행률이 아니다. */
export const progress = (snap: Snapshot) => {
  const live = snap.tasks.filter(t => t.state !== 'CANCELLED').length
  return { done: snap.completed, of: live, pct: live ? Math.round((snap.completed / live) * 100) : null }
}

/** 상태 줄 한 줄. 기호만 쓰지 않고 낱말로 쓴다(기호만으로는 뜻을 알기 어렵다). */
export const statusText = (snap: Snapshot | null, meta: SnapMeta, runs: Run[], warns: Anomaly[] = []) => {
  if (!snap) return meta.error ? `자율 · ${fit(meta.error, 40)}` : '자율 · 상태 없음'
  const p = progress(snap)
  const blocked = snap.counts.BLOCKED ?? 0
  const parts = [`자율 완료 ${p.done}/${p.of}`, `실행 ${runningRuns(runs).length}/배정 ${slotUsed(snap)}`]
  const wait = awaiting(snap).length + openDecisions(snap).length
  if (wait) parts.push(`답 필요 ${wait}`)
  if (blocked) parts.push(`보류 ${blocked}`)
  if (warns.length) parts.push(`경고 ${warns.length}`)
  if (snap.paused) parts.push('일시 중지')
  if (meta.stale) parts.push('갱신 실패')
  return parts.join(' · ')
}

/** 지금 무엇이 흐름을 막는지. 스냅샷의 숫자만으로 정한다(추측하지 않는다). */
export const bottlenecks = (snap: Snapshot): string[] => {
  const c = (s: string) => snap.counts[s] ?? 0
  const out: string[] = []
  const impl = snap.slots.implement
  const readyWaiting = snap.tasks.filter(t => (t.state === 'READY' || t.state === 'RETRY') && t.waiting?.includes('슬롯')).length
  if (impl && readyWaiting > 0) out.push(`구현 슬롯 ${impl.used ?? 0}/${impl.limit} 가득, 준비된 작업 ${readyWaiting}개 대기`)
  const verify = snap.slots.verify
  if (verify && c('VERIFYING') > verify.limit) out.push(`검증 대기 ${c('VERIFYING')}개 > 검증 슬롯 ${verify.limit}`)
  if (c('READY_TO_MERGE') > 1) out.push(`통합 대기 ${c('READY_TO_MERGE')}개 (통합은 한 번에 하나)`)
  const userWait = awaiting(snap).length + openDecisions(snap).length
  if (userWait > 0) out.push(`사용자 검토·결정 대기 ${userWait}건`)
  if (c('BLOCKED') > 0) out.push(`보류 ${c('BLOCKED')}개`)
  if (snap.paused) out.push(`새 배정 일시 중지: ${snap.paused.reason}`)
  return out
}

export const changes = (before: Snapshot | null, after: Snapshot): string[] => {
  if (!before) return []
  const was = new Map(before.tasks.map(t => [t.id, t.state]))
  const out: string[] = []
  for (const t of after.tasks) {
    if (was.get(t.id) === t.state) continue
    if (t.state === 'AWAITING_APPROVAL') out.push(`${t.id} 미리보기 검토 대기`)
    if (t.state === 'BLOCKED') out.push(`${t.id} 보류: ${fit(t.reason, 40)}`)
  }
  const open = new Set(openDecisions(before).map(d => d.id))
  for (const d of openDecisions(after)) if (!open.has(d.id)) out.push(`${d.id} 결정 필요: ${fit(d.title, 30)}`)
  return out
}

// ---------------------------------------------------------------- 이상 감지 (판단하지 않고 숫자로만 경고한다)

export const REPEAT_FAILS = 3

/** 도구 호출 묶음을 실행 기록에 더한다. 연속 실패는 묶음 안의 순서를 따른다. */
export type ToolBatch = { count: number; last: string; errors: number; lastAt: number; tailFails: number; allFailed: boolean }
export const applyBatch = (r: Run, b: ToolBatch): Run => ({
  ...r,
  tools: r.tools + b.count,
  lastTool: b.last || r.lastTool,
  errors: r.errors + b.errors,
  lastActiveMs: Math.max(r.lastActiveMs, b.lastAt),
  failStreak: b.allFailed ? r.failStreak + b.tailFails : b.tailFails,
})
export const addCall = (b: ToolBatch | undefined, summary: string, failed: boolean, at: number): ToolBatch => {
  const x = b ?? { count: 0, last: '', errors: 0, lastAt: 0, tailFails: 0, allFailed: true }
  return {
    count: x.count + 1, last: summary, errors: x.errors + (failed ? 1 : 0), lastAt: Math.max(x.lastAt, at),
    tailFails: failed ? x.tailFails + 1 : 0, allFailed: x.allFailed && failed,
  }
}

export const anomalies = (snap: Snapshot | null, runs: Run[], now: number, stallMs: number): Anomaly[] => {
  const out: Anomaly[] = (snap?.anomalies ?? []).map(a => ({ kind: 'long-stage', task: a.task, agentId: null, text: a.text }))
  for (const r of runningRuns(runs)) {
    const who = `${r.role}${r.task ? ` ${r.task}` : ''}`
    const idle = now - r.lastActiveMs
    if (idle >= stallMs) out.push({ kind: 'stall', task: r.task, agentId: r.agentId, text: `${who} ${fmtDur(idle)}째 도구 호출 없음` })
    if (r.failStreak >= REPEAT_FAILS) out.push({ kind: 'repeat-fail', task: r.task, agentId: r.agentId, text: `${who} 도구 호출 ${r.failStreak}번 연속 실패·거부 (${r.lastTool})` })
  }
  for (const t of unobservedHolders(snap, runs)) {
    out.push({ kind: 'ghost', task: t.id, agentId: null, text: `${t.id} ${mark(t.state).label} 슬롯을 잡았지만 이 세션에서 실행이 보이지 않는다` })
  }
  return out
}
export const anomalyKey = (a: Anomaly) => `${a.kind}:${a.task ?? ''}:${a.agentId ?? ''}`

export const pushActivity = (list: Activity[], items: Activity[], max: number) => [...list, ...items].slice(-Math.max(50, Math.min(1000, max)))

// ---------------------------------------------------------------- 패널 줄

const head = (text: string): Line => [{ text, tone: 'accent', bold: true }]
const kv = (k: string, v: string, tone: Tone = 'plain'): Line => [{ text: `${k} `, tone: 'dim' }, { text: v, tone }]

// 확인할 때마다 한 칸씩 반짝이는 별(aster). 모두 한 칸 문자다
const SPIN = ['·', '✧', '✦', '✧']
/** `── 제목 ─────` 구분선. */
export const section = (title: string, width: number): Line => [
  { text: `── ${title} `, tone: 'accent', bold: true },
  { text: '─'.repeat(Math.max(0, width - strWidth(title) - 4)), tone: 'dim' },
]
export const rule = (width: number): Line => [{ text: '─'.repeat(Math.max(0, width)), tone: 'dim' }]
export const bar = (done: number, of: number, cells: number) => {
  const n = of ? Math.min(cells, Math.round((done / of) * cells)) : 0
  return '█'.repeat(n) + '░'.repeat(cells - n)
}
/** 폭에 맞춰 낱말 단위로 여러 줄로 나눈다(한 낱말이 폭보다 길면 자른다). 넘치면 마지막 줄 끝에 …를 붙인다. */
export const wrapText = (s: string, w: number, maxLines: number): string[] => {
  const width = Math.max(4, w)
  const out: string[] = []
  let cur = ''
  for (let word of s.split(/\s+/).filter(Boolean)) {
    const next = cur ? `${cur} ${word}` : word
    if (strWidth(next) <= width) {
      cur = next
      continue
    }
    if (cur) out.push(cur)
    while (strWidth(word) > width) {
      const head = fit(word, width + 1).slice(0, -1) // …를 붙이지 않고 폭만큼 자른다
      out.push(head)
      word = word.slice(head.length)
    }
    cur = word
  }
  if (cur) out.push(cur)
  if (out.length <= maxLines) return out
  const kept = out.slice(0, maxLines)
  kept[maxLines - 1] = fit(`${kept[maxLines - 1]}${out[maxLines]}`, width)
  return kept
}

/**
 * 모든 탭 위에 놓는 머리말. 오른쪽 위 회전 표시는 Mod가 스냅샷 파일을 확인할 때마다 한 칸 돈다.
 * 숫자가 그대로여도 표시가 돌면 자동 갱신이 살아 있다는 뜻이다.
 */
export const headerLines = (snap: Snapshot, meta: SnapMeta, runs: Run[], now: number, warns: Anomaly[], width: number, refreshMs: number, usage: Usage | null = null): Line[] => {
  const p = progress(snap)
  const live: Seg = meta.stale
    ? { text: '✗ 갱신 실패', tone: 'err', bold: true }
    : { text: `${SPIN[Math.floor(meta.checkedMs / Math.max(1, refreshMs)) % SPIN.length]} 실시간 · 변화 ${ago(ms(snap.generated_at), now)}`, tone: 'ok' }
  const left = fitLine(
    [{ text: '✦ ASTERBRAID ', tone: 'accent', bold: true }, { text: `${snap.project.name} · ${snap.integration.branch ?? '통합 브랜치 없음'} ${short(snap.integration.head)}`, tone: 'dim' }],
    Math.max(0, width - strWidth(live.text) - 1),
  )
  const lines: Line[] = [[...left, { text: ' '.repeat(Math.max(1, width - lineWidth(left) - strWidth(live.text))) }, live]]
  lines.push([
    { text: '진행 ', tone: 'dim' },
    { text: bar(p.done, p.of, 14), tone: 'ok' },
    { text: ` ${p.done}/${p.of} 완료`, bold: true },
    { text: p.pct === null ? '' : ` ${p.pct}% (작업 수 기준)`, tone: 'dim' },
  ])
  const missing = unobservedHolders(snap, runs).length
  lines.push([
    { text: `실행 ${runningRuns(runs).length} · 배정 ${slotUsed(snap)} · 검증 ${snap.counts.VERIFYING ?? 0} · 통합 대기 ${snap.counts.READY_TO_MERGE ?? 0}`, tone: 'dim' },
    ...(missing ? [{ text: ` · 실행 미확인 ${missing}`, tone: 'warn' as Tone }] : []),
    // 메인 세션(manager)의 컨텍스트. 값을 못 받으면 숨긴다
    ...(usage?.contextPct != null ? [{ text: ` · 컨텍스트 ${Math.round(usage.contextPct)}%`, tone: (usage.contextPct >= 80 ? 'warn' : 'dim') as Tone, bold: usage.contextPct >= 80 }] : []),
  ])
  if (snap.away) lines.push([{ text: `☾ 자리 비움 중${snap.away.previews ? ' · 미리보기 자동 승인' : ''}`, tone: 'info', bold: true }, { text: '  → 7 명령 탭에서 복귀', tone: 'dim' }])
  else if (pendingAway(snap).length) lines.push([{ text: `☾ 자리 비운 동안 정한 것 ${pendingAway(snap).length}건 확인`, tone: 'info', bold: true }, { text: '  → 4 승인·결정 탭', tone: 'dim' }])
  const wait = awaiting(snap).length
  const dec = openDecisions(snap).length
  if (wait || dec) lines.push([{ text: `▶ 사용자 차례: 미리보기 승인 ${wait} · 결정 ${dec}`, tone: 'warn', bold: true }, { text: '  → 4 승인·결정 탭', tone: 'dim' }])
  if (snap.paused) lines.push([{ text: `⏸ 새 배정 일시 중지: ${snap.paused.reason}`, tone: 'warn' }, { text: '  → 7 명령 탭에서 재개', tone: 'dim' }])
  const blocked = snap.counts.BLOCKED ?? 0
  if (blocked) lines.push([{ text: `✗ 보류 ${blocked}건`, tone: 'err', bold: true }, { text: '  → 2 작업 탭', tone: 'dim' }])
  for (const w of warns.slice(0, 2)) lines.push([{ text: `⚠ ${w.text}`, tone: 'warn' }])
  if (warns.length > 2) lines.push([{ text: `⚠ 외 ${warns.length - 2}건 → 6 활동 탭`, tone: 'warn' }])
  if (meta.stale) lines.push([{ text: `✗ ${meta.error ?? '스냅샷 읽기 실패'} (이전 화면을 보이는 중)`, tone: 'err' }])
  return [...lines.map(l => fitLine(l, width)), rule(width)]
}

const ORDER: Record<string, number> = { IMPLEMENTING: 0, RETRY: 0, VERIFYING: 1, MERGING: 1, READY_TO_MERGE: 1, PREVIEWING: 2, DESIGNING: 2, RESEARCHING: 2, AWAITING_APPROVAL: 3, BLOCKED: 4, READY: 5, PENDING: 6, COMPLETED: 7 }

/** 현황 탭: 작업 표, manager 설명, 병목, 최근 변화. 숫자 요약은 머리말에 있다. */
export const overviewLines = (snap: Snapshot, runs: Run[], now: number, width: number): Line[] => {
  const lines: Line[] = [section('작업', width)]
  const sorted = snap.tasks.filter(t => t.state !== 'CANCELLED').sort((a, b) => (ORDER[a.state] ?? 9) - (ORDER[b.state] ?? 9) || b.priority - a.priority)
  const live = sorted.filter(t => t.state !== 'COMPLETED')
  const doneN = sorted.length - live.length
  // 진행 중인 것이 있으면 완료는 한 줄로 접는다(쌓이면 진행 중인 것이 안 보인다)
  const tasks = live.length ? live : sorted
  if (!sorted.length) lines.push([{ text: '등록된 작업이 없다. manager가 sched add로 등록한다', tone: 'dim' }])
  const titleW = Math.max(8, width - 2 - 9 - 9 - 22)
  for (const t of tasks.slice(0, 8)) {
    const m = mark(t.state)
    const r = runningRuns(runs).find(x => x.task === t.id)
    const latest = t.preview.versions[t.preview.versions.length - 1]
    const who = r
      ? `${r.role} · ${fmtDur(now - r.startedMs)}`
      : t.decisions_open.length ? `결정 대기 ${t.decisions_open.join(',')}`
      : t.state === 'AWAITING_APPROVAL' && latest ? `v${latest.n} 검토 대기`
      : RUNNING_STATES[t.state] ? '실행 안 보임'
      : t.state === 'BLOCKED' ? fit(t.reason, 22)
      : t.state === 'COMPLETED' ? ''
      : t.runnable ? '시작 가능' : fit(t.waiting ?? '', 22)
    const whoTone: Tone = r ? 'accent' : RUNNING_STATES[t.state] || t.state === 'BLOCKED' ? 'warn' : 'dim'
    lines.push([
      { text: `${m.mark} `, tone: m.tone },
      { text: `${pad(t.id, 8)} `, bold: true },
      { text: `${pad(m.label, 8)} `, tone: m.tone },
      { text: `${pad(t.title, titleW)} `, tone: t.state === 'COMPLETED' ? 'dim' : 'plain' },
      { text: who, tone: whoTone },
    ])
  }
  if (tasks.length > 8) lines.push([{ text: `  외 ${tasks.length - 8}개 → 2 작업 탭`, tone: 'dim' }])
  if (live.length && doneN) lines.push([{ text: `✓ 완료 ${doneN}개 → 2 작업 탭`, tone: 'dim' }])
  const note = snap.notes[snap.notes.length - 1]
  if (note) {
    lines.push(section(`manager · ${note.kind}${note.task ? ` · ${note.task}` : ''}`, width))
    for (const t of wrapText(note.text, width, 3)) lines.push([{ text: t }])
  }
  const b = bottlenecks(snap)
  if (b.length) {
    lines.push(section('병목', width))
    for (const x of b) lines.push([{ text: `· ${x}`, tone: 'warn' }])
  }
  if (snap.events.length) {
    lines.push(section('최근 변화', width))
    for (const ev of snap.events.slice(-3).reverse()) {
      const m = mark(ev.to)
      lines.push([
        { text: `${m.mark} `, tone: m.tone },
        { text: `${ev.task} ${mark(ev.from).label}→${m.label} `, bold: true },
        { text: `${ago(ms(ev.at), now)}${ev.reason ? ` · ${ev.reason}` : ''}`, tone: 'dim' },
      ])
    }
  }
  return lines.map(l => fitLine(l, width))
}

/** 대기열 행. id가 있는 행은 작업의 첫 줄이라 눌러서 펼칠 수 있다. */
export type Row = { line: Line; id?: string }

const DONE_SHOWN = 20
const isDone = (t: SnapTask) => t.state === 'COMPLETED' || t.state === 'CANCELLED'

/** 완료 작업의 걸린 시간(첫 배정→완료, 대기 포함)과 토큰. 모르면 빈 문자열. */
export const costText = (t: SnapTask, tok: TaskTokens) => {
  const a = ms(t.started ?? null)
  const b = ms(t.completed)
  const s = taskTokenSummary(tok, t.id)
  const parts = [
    ...(a !== null && b !== null && b >= a ? [`⏱ ${fmtDur(b - a)}`] : []),
    ...(s ? [`${s.partial ? '≥' : ''}${Math.round(s.tokens / 1000)}k tok · 에이전트 ${s.agents}`] : []),
  ]
  return parts.join(' · ')
}

/**
 * 진행 중·대기·보류를 위에, 완료·취소는 아래에 접어 둔다(showDone이면 최근 완료 20개까지).
 * 필터가 '완료'면 완료만 펼친다.
 */
export const queueLines = (snap: Snapshot, filter: QueueFilter, expanded: string | null, width: number, showDone = false, tok: TaskTokens = {}): Row[] => {
  const matched = snap.tasks.filter(t => inFilter(t, filter))
  if (matched.length === 0) return [{ line: [{ text: '해당하는 작업이 없다', tone: 'dim' }] }]
  const live = matched.filter(t => !isDone(t)).sort((a, b) => (ORDER[a.state] ?? 9) - (ORDER[b.state] ?? 9) || b.priority - a.priority)
  const done = matched.filter(isDone).sort((a, b) => (b.completed ?? b.updated).localeCompare(a.completed ?? a.updated))
  const out: Row[] = []
  const push = (line: Line, id?: string) => out.push({ line: fitLine(line, width), id })
  const open = filter === 'done' || showDone
  const draw = (t: SnapTask) => {
    const m = mark(t.state)
    const cost = t.state === 'COMPLETED' ? costText(t, tok) : ''
    push([
      { text: `${m.mark} `, tone: m.tone },
      { text: pad(t.id, 10), bold: true },
      { text: pad(m.label, 8), tone: m.tone },
      { text: `p${t.priority} ${t.risk}/${t.profile} ↻${t.retries} `, tone: 'dim' },
      ...(t.profile === 'fast' ? [{ text: '작게 ', tone: 'accent' as Tone }] : []),
      { text: t.title },
      ...(cost ? [{ text: `  ${cost}`, tone: 'dim' as Tone }] : []),
    ], t.id)
    const why = t.decisions_open.length ? `결정 대기 ${t.decisions_open.join(',')}` : t.waiting
    if (why) push([{ text: `    ⋯ ${why}`, tone: t.runnable ? 'ok' : 'warn' }])
    else if (t.runnable) push([{ text: `    ▶ ${POOL_LABEL[t.pool ?? ''] ?? t.pool} 시작 가능`, tone: 'ok' }])
    if (t.deps.length) push([{ text: `    ← ${t.deps.map(d => `${d}${mark(snap.tasks.find(x => x.id === d)?.state ?? '').mark}`).join(' ')}`, tone: 'dim' }])
    if (expanded === t.id) {
      push(kv('    담당', t.paths.join(', ')))
      if (t.forbid.length) push(kv('    금지', t.forbid.join(', ')))
      push(kv('    완료 기준', t.criteria))
      if (t.design_depth) push(kv('    설계', `${t.design_depth}${t.notes?.length ? ` · 노트 ${t.notes[t.notes.length - 1]}` : ''}`))
      push(kv('    배정', `${t.agent ?? '-'} · 시도 ${t.attempts ?? 0}`))
      if (t.reason) push(kv('    사유', t.reason))
    }
  }
  live.forEach(draw)
  if (done.length && filter !== 'done') {
    const c = done.filter(t => t.state === 'CANCELLED').length
    push([{ text: `완료 ${done.length - c}${c ? ` · 취소 ${c}` : ''}${open ? '' : ' (접힘)'}`, tone: 'accent', bold: true }])
  }
  if (open) done.slice(0, DONE_SHOWN).forEach(draw)
  if (open && done.length > DONE_SHOWN) push([{ text: `  이전 ${done.length - DONE_SHOWN}개는 sched status로 본다`, tone: 'dim' }])
  return out
}

// ---------------------------------------------------------------- 설정

export type Config = {
  openOnStart: boolean
  layout: Layout
  paneColumns: number
  refreshMs: number
  allowActions: boolean
  toolDetail: 'names' | 'masked'
  launcher: string
  maxEvents: number
  statusLine: boolean
  notify: boolean
  stallMs: number
  tellManager: boolean
}

const clamp = (v: unknown, lo: number, hi: number, d: number) => (typeof v === 'number' && Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : d)
export const parseConfig = (o: Readonly<Record<string, unknown>>): Config => ({
  openOnStart: o.openOnStart !== false,
  layout: (['auto', 'mini', 'compact', 'wide'] as const).includes(o.layout as Layout) ? (o.layout as Layout) : 'auto',
  paneColumns: clamp(o.paneColumns, 40, 200, 90),
  refreshMs: clamp(o.refreshSeconds, 1, 30, 1) * 1000,
  allowActions: o.allowActions !== false,
  toolDetail: o.toolDetail === 'masked' ? 'masked' : 'names',
  launcher: ['uv', 'python', 'python3', 'py'].includes(o.launcher as string) ? (o.launcher as string) : 'uv',
  maxEvents: clamp(o.maxEvents, 50, 1000, 300),
  statusLine: o.statusLine !== false,
  notify: o.notify !== false,
  stallMs: clamp(o.stallMinutes, 1, 120, 10) * 60_000,
  tellManager: o.tellManager === true,
})

/** 대기(다시 쓰일 수 있는 idle)·끝남(끝났거나 더 쓰일 일이 없는 idle) 에이전트. */
export const doneGroups = (snap: Snapshot | null, runs: Run[]) => {
  const rest = runs.filter(r => r.status !== 'running').reverse()
  return {
    idle: rest.filter(r => r.status === 'idle' && !retired(snap, r)),
    ended: rest.filter(r => r.status !== 'idle' || retired(snap, r)),
  }
}

/**
 * 에이전트 목록. 실행 중인 것을 위에 펼치고, 대기·끝난 것은 showDone일 때만 그 아래에 그린다
 * (섞여 있으면 지금 도는 것이 한눈에 안 보인다).
 */
export const agentLines = (snap: Snapshot | null, runs: Run[], now: number, expanded: string | null, showDone = false): Line[] => {
  const out: Line[] = []
  const running = runningRuns(runs)
  const g = doneGroups(snap, runs)
  if (runs.length === 0) out.push([{ text: '이 세션에서 관측한 서브에이전트가 없다', tone: 'dim' }])
  else out.push(head(`실행 중 ${running.length}`))
  if (runs.length && !running.length) out.push([{ text: '  지금 실행 중인 에이전트가 없다', tone: 'dim' }])
  const draw = (r: Run) => {
    const done = retired(snap, r)
    const tone: Tone = r.status === 'running' ? 'accent' : r.status === 'completed' || done ? 'ok' : r.status === 'idle' ? 'dim' : 'err'
    const sym = r.status === 'running' ? '●' : r.status === 'completed' || done ? '✓' : r.status === 'idle' ? '◌' : '✗'
    const round = poolRound(snap, r)
    out.push([
      { text: `${sym} `, tone },
      { text: pad(r.role, 12), bold: true },
      { text: r.task ? `${r.task} ${POOL_LABEL[r.pool ?? ''] ?? r.pool}${round && round > 1 ? ` ${round}회차` : ''}` : '미연결(marker 없음)', tone: r.task ? 'plain' : 'warn' },
      { text: ` · ${done ? '작업 끝남(팀원 대기) · ' : r.status === 'idle' ? '일 끝남(대기) · ' : ''}${fmtDur((r.endedMs ?? now) - r.startedMs)} · 도구 ${r.tools}${r.errors ? ` · 오류 ${r.errors}` : ''}${r.tokens !== null ? ` · ${Math.round(r.tokens / 1000)}k tok` : ''}`, tone: 'dim' },
    ])
    out.push([{ text: `    ${r.lastTool || r.description}`, tone: 'dim' }])
    if (expanded === r.agentId) {
      out.push(kv('    id', r.agentId))
      out.push(kv('    폴더', r.cwd || '-'))
      const t = r.task ? snap?.tasks.find(x => x.id === r.task) : undefined
      if (t) out.push(kv('    브랜치', `${t.branch ?? '-'} @ ${short(t.head)} · 스케줄러 상태 ${t.state}`))
    }
  }
  running.slice(0, 20).forEach(draw)
  if (g.idle.length || g.ended.length) {
    out.push(head(`대기 ${g.idle.length} · 끝남 ${g.ended.length}${showDone ? '' : ' (접힘)'}`))
    if (showDone) [...g.idle, ...g.ended].slice(0, 20).forEach(draw) // 대기(다시 쓰일 수 있는 것) 먼저
  }
  const missing = unobservedHolders(snap, runs)
  if (missing.length) {
    out.push(head('슬롯은 잡혔지만 이 세션에서 실행이 관측되지 않은 작업'))
    for (const t of missing) out.push([{ text: `○ ${t.id} ${mark(t.state).label} · 배정 ${t.agent ?? '-'} #${t.attempts ?? 0}`, tone: 'warn' }])
    out.push([{ text: '  다른 세션에서 실행 중이거나, 세션이 끊겼을 수 있다(manager가 sched recover로 확인)', tone: 'dim' }])
  }
  return out
}

const LANE: Record<string, string> = {
  PENDING: '·', READY: '·', RESEARCHING: 'r', DESIGNING: 'd', PREVIEWING: 'p', AWAITING_APPROVAL: '?', IMPLEMENTING: '█',
  RETRY: '↻', VERIFYING: '▒', READY_TO_MERGE: '⇢', MERGING: '≡', BLOCKED: 'x', COMPLETED: ' ', CANCELLED: ' ',
}

/** 기록된 시작·끝 시각만으로 그린 레인. 칸 하나는 (전체 구간 / 폭)이다. */
export const timelineLines = (snap: Snapshot, now: number, width: number): Line[] => {
  const tasks = snap.tasks.filter(t => t.timeline.length).slice(0, 16)
  if (!tasks.length) return [[{ text: '기록된 상태 변화가 없다', tone: 'dim' }]]
  const starts = tasks.map(t => ms(t.timeline[0]?.start) ?? now)
  const from = Math.min(...starts)
  const span = Math.max(1000, now - from)
  const cols = Math.max(10, width - 12)
  const out: Line[] = [[{ text: `${pad('', 11)}${fmtDur(span)} 전 → 지금 (칸당 ${fmtDur(span / cols)})`, tone: 'dim' }]]
  for (const t of tasks) {
    const cells = Array<string>(cols).fill(' ')
    for (const seg of t.timeline) {
      const a = ms(seg.start)
      if (a === null) continue
      const b = ms(seg.end) ?? now
      const i0 = Math.floor(((a - from) / span) * cols)
      const i1 = Math.max(i0 + 1, Math.ceil(((b - from) / span) * cols))
      for (let i = Math.max(0, i0); i < Math.min(cols, i1); i++) cells[i] = LANE[seg.state] ?? '?'
    }
    out.push([{ text: `${pad(t.id, 10)} `, bold: true }, { text: cells.join(''), tone: mark(t.state).tone }])
  }
  out.push([{ text: 'r 조사 d 설계 p 미리보기 ? 승인대기 █ 구현 ↻ 재작업 ▒ 검증 ⇢ 병합대기 ≡ 병합 x 보류 · 대기', tone: 'dim' }])
  return out
}

export const gitLines = (snap: Snapshot, width: number): Line[] => {
  const out: Line[] = [kv('통합 브랜치', `${snap.integration.branch ?? '-'} @ ${short(snap.integration.head, 10)}`)]
  const live = snap.tasks.filter(t => t.branch && t.state !== 'CANCELLED' && !(t.state === 'COMPLETED' && t.cleaned))
  out.push(head('작업 브랜치'))
  if (!live.length) out.push([{ text: '없음', tone: 'dim' }])
  for (const t of live) {
    const m = mark(t.state)
    const ev = t.evidence.valid === null ? '' : ` · 검증 ${t.evidence.valid}/${t.evidence.required}`
    out.push([{ text: `${m.mark} `, tone: m.tone }, { text: `${pad(t.id, 10)} `, bold: true }, { text: `${t.branch} ${short(t.base)}→${short(t.head)}${ev}`, tone: 'dim' }])
    if (t.state === 'COMPLETED' && !t.cleaned) out.push([{ text: '    정리 가능(sched cleanup이 조건을 다시 확인한다)', tone: 'dim' }])
  }
  const queue = snap.tasks.filter(t => t.state === 'READY_TO_MERGE' || t.state === 'MERGING')
  out.push(head('통합 대기열'))
  out.push(queue.length ? [{ text: queue.map(t => `${mark(t.state).mark}${t.id}`).join(' → ') }] : [{ text: '비었다', tone: 'dim' }])
  out.push(head('최근 통합 결과'))
  const log = snap.integration_log.slice(-8).reverse()
  if (!log.length) out.push([{ text: '기록 없음', tone: 'dim' }])
  for (const e of log) {
    const tone: Tone = e.status === 'MERGED' ? 'ok' : e.status === 'BUSY' ? 'dim' : 'err'
    out.push([{ text: `${pad(e.status, 9)}`, tone, bold: true }, { text: `${pad(e.task, 10)} ` }, { text: fit(e.message.split('\n')[0] ?? '', 60), tone: 'dim' }])
  }
  return out.map(l => fitLine(l, width))
}

export const metricLines = (snap: Snapshot, runs: Run[], usage: { costUsd: number | null; contextPct: number | null }): Line[] => {
  const m = snap.metrics as Record<string, unknown>
  const num = (k: string) => (typeof m[k] === 'number' ? (m[k] as number) : null)
  const p = progress(snap)
  const peakObserved = peakRunning(runs)
  const tok = runs.reduce((n, r) => n + (r.tokens ?? 0), 0)
  const by = (m.seconds_by_state ?? {}) as Record<string, number>
  const lines: Line[] = [
    head('스케줄러 기록 (sched.py)'),
    kv('완료율', p.pct === null ? '-' : `${p.pct}% (${p.done}/${p.of}, 작업 수 기준)`),
    kv('평균 리드타임', num('avg_lead_time_s') === null ? '-' : fmtDur(num('avg_lead_time_s')! * 1000)),
    kv('구현 동시 실행 최대', `${num('implement_peak_parallel') ?? '-'} / 슬롯 ${num('implement_slot_limit') ?? '-'}`),
    kv('구현 슬롯 활용률', num('implement_slot_utilization') === null ? '-' : `${Math.round(num('implement_slot_utilization')! * 100)}% (지금 한도 기준)`),
    kv('재시도 합계', String(num('retries_total') ?? 0)),
    kv('검증 실패율', num('verify_fail_rate') === null ? '-' : `${Math.round(num('verify_fail_rate')! * 100)}%`),
    kv('병합 충돌 · 통합 테스트 실패', `${num('merge_conflicts') ?? 0} · ${num('integration_test_failures') ?? 0}`),
    kv('상태별 누적', Object.entries(by).map(([k, v]) => `${mark(k).label} ${fmtDur(v * 1000)}`).join(' · ') || '-'),
    head('이 세션에서 관측 (Mod)'),
    kv('에이전트 동시 실행 최대', String(peakObserved)),
    kv('에이전트 토큰 합', tok ? `${Math.round(tok / 1000)}k (각 에이전트 최근 요청 입력 + 출력, Claude Code 보고값)` : '-'),
    kv('세션 비용', usage.costUsd === null ? '-' : `$${usage.costUsd.toFixed(2)} (추정, 청구액 아님)`),
    kv('컨텍스트', usage.contextPct === null ? '-' : `${Math.round(usage.contextPct)}%`),
  ]
  return lines
}

export const peakRunning = (runs: Run[]) => {
  const pts = runs.flatMap(r => [[r.startedMs, 1], [r.endedMs ?? Number.MAX_SAFE_INTEGER, -1]] as [number, number][]).sort((a, b) => a[0] - b[0] || a[1] - b[1])
  let cur = 0
  let peak = 0
  for (const [, d] of pts) {
    cur += d
    peak = Math.max(peak, cur)
  }
  return peak
}

export const activityLines = (snap: Snapshot | null, activity: Activity[], errorsOnly: boolean, now: number, width: number): Line[] => {
  type Row = { at: number; level: Activity['level']; text: string }
  const rows: Row[] = activity.map(a => ({ at: a.atMs, level: a.level, text: `${a.task ? `${a.task} ` : ''}${a.text}` }))
  for (const e of snap?.events ?? []) {
    const level = e.to === 'BLOCKED' ? 'error' : e.to === 'RETRY' ? 'warn' : 'info'
    rows.push({ at: ms(e.at) ?? 0, level, text: `${e.task} ${e.from}→${e.to}${e.reason ? `: ${e.reason}` : ''}` })
  }
  for (const e of snap?.integration_log ?? []) {
    rows.push({ at: ms(e.time) ?? 0, level: e.status === 'MERGED' ? 'info' : e.status === 'BUSY' ? 'warn' : 'error', text: `통합 ${e.status} ${e.task}: ${e.message.split('\n')[0] ?? ''}` })
  }
  for (const d of snap?.decisions ?? []) {
    if (d.state === 'ANSWERED') rows.push({ at: ms(d.answered_at) ?? 0, level: 'info', text: `결정 ${d.id} → ${d.choice} (${d.by})` })
  }
  const shown = rows.filter(r => !errorsOnly || r.level !== 'info').sort((a, b) => b.at - a.at).slice(0, 40)
  if (!shown.length) return [[{ text: errorsOnly ? '오류·경고가 없다' : '활동이 없다', tone: 'dim' }]]
  return shown.map(r =>
    fitLine(
      [
        { text: r.level === 'error' ? '✗ ' : r.level === 'warn' ? '! ' : '· ', tone: r.level === 'error' ? 'err' : r.level === 'warn' ? 'warn' : 'dim' },
        { text: `${pad(ago(r.at, now), 9)} `, tone: 'dim' },
        { text: redact(stripControl(r.text)).replace(/\s+/g, ' ') },
      ],
      width,
    ),
  )
}

export const miniLines = (snap: Snapshot, meta: SnapMeta, runs: Run[], width: number, warns: Anomaly[] = []): Line[] => {
  const p = progress(snap)
  const lines: Line[] = [
    [
      { text: '✦ AsterBraid ', tone: 'accent', bold: true },
      { text: `✓ ${p.done}/${p.of}`, tone: 'ok' },
      { text: ` · 실행 ${runningRuns(runs).length} · 슬롯 ${slotUsed(snap)} · 검증 ${snap.counts.VERIFYING ?? 0} · 통합대기 ${snap.counts.READY_TO_MERGE ?? 0}` },
    ],
  ]
  const userWait = awaiting(snap).length + openDecisions(snap).length
  const blocked = snap.counts.BLOCKED ?? 0
  if (userWait || blocked || warns.length || snap.paused || meta.stale) {
    lines.push([
      ...(userWait ? [{ text: `? 사용자 차례 ${userWait} `, tone: 'warn' as Tone, bold: true }] : []),
      ...(blocked ? [{ text: `✗ 보류 ${blocked} `, tone: 'err' as Tone }] : []),
      ...(warns.length ? [{ text: `⚠ 경고 ${warns.length} `, tone: 'warn' as Tone }] : []),
      ...(snap.paused ? [{ text: '⏸ 일시 중지 ', tone: 'warn' as Tone }] : []),
      ...(meta.stale ? [{ text: `! stale ${meta.error ?? ''}`, tone: 'err' as Tone }] : []),
    ])
  }
  return lines.map(l => fitLine(l, width))
}
