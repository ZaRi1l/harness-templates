// asterbraid-dashboard의 상태 계약. 스냅샷 타입은 sched.py snapshot()의 schema_version 1을 따른다.

export type TaskState =
  | 'PENDING' | 'RESEARCHING' | 'DESIGNING' | 'PREVIEWING' | 'AWAITING_APPROVAL' | 'READY' | 'IMPLEMENTING'
  | 'VERIFYING' | 'READY_TO_MERGE' | 'MERGING' | 'RETRY' | 'BLOCKED' | 'COMPLETED' | 'CANCELLED'

export type PreviewVersion = { n: number; file: string; at: string; summary?: string; sha256: string }

export type SnapTask = {
  id: string
  title: string
  state: TaskState
  priority: number
  risk: string
  profile: string
  paths: string[]
  forbid: string[]
  deps: string[]
  criteria: string
  agent: string | null
  attempts: number | null
  branch: string | null
  worktree: string | null
  base: string | null
  head: string | null
  retries: number
  reason: string
  created: string
  updated: string
  completed: string | null
  cleaned: boolean | null
  /** 설계 깊이(light|standard|deep). 설계가 필요 없으면 null, 옛 스냅샷에는 없다 */
  design_depth?: string | null
  design_review?: { verdict: string; by: string | null; at: string; notes?: string; decision?: string } | null
  review_pending?: boolean
  verifiers_assigned?: string[]
  /** 조사·설계 노트 경로(sched done --notes) */
  notes?: string[]
  /** 첫 배정 시각(걸린 시간 계산). 옛 스냅샷에는 없다 */
  started?: string | null
  pool: string | null
  runnable: boolean
  waiting: string | null
  decisions_open: string[]
  evidence: { valid: number | null; required: number; items: { verifier: string; verdict: string; sha: string; at: string }[] }
  preview: {
    required: boolean
    versions: PreviewVersion[]
    approved: number | null
    feedback: { at: string; on: number; text: string }[]
    approval?: { version: number; sha256: string; at: string; by: string }
  }
  timeline: { state: string; start: string; end: string | null }[]
}

export type Decision = {
  id: string
  title: string
  question: string
  kind: string
  tasks: string[]
  options: { key: string; label: string; pros: string; cons: string }[]
  recommend: string | null
  why: string
  state: 'OPEN' | 'ANSWERED' | 'CANCELLED'
  created: string
  choice: string | null
  note: string | null
  by: string | null
  answered_at: string | null
  hold?: 'start' | 'merge'
  ask_user?: boolean
}

export type Snapshot = {
  schema: string
  schema_version: number
  generated_at: string
  generated_ms: number
  project: { root: string; name: string; state_dir: string }
  integration: { branch: string | null; head: string | null }
  paused: { at: string; reason: string } | null
  /** 사용자 자리 비움(sched away). 옛 스냅샷에는 없다 */
  away?: { at: string; previews: boolean; reason: string } | null
  /** 자리 비움 중 추천안으로 정한 것. reviewed: null(확인 전) | keep | redo:<새 결정 ID> */
  away_log?: AwayEntry[]
  design_review_mode?: string
  total: number
  completed: number
  counts: Record<string, number>
  slots: Record<string, { used?: number; queued?: number; limit: number }>
  tasks: SnapTask[]
  decisions: Decision[]
  notes: { at: string; kind: string; text: string; task: string | null }[]
  events: { task: string; at: string; from: string; to: string; reason: string; kind?: string; agent?: string; pool?: string; attempt?: number }[]
  integration_log: { time: string; status: string; task: string; message: string }[]
  metrics: Record<string, unknown>
  anomalies: { kind: 'long-stage'; task: string; state: string; seconds: number; median_s: number; text: string }[]
  dashboard_html: string
}

export type AwayEntry = { at: string; kind: 'decision' | 'preview'; key: string; text: string; reviewed: string | null; choice?: string; version?: number }

/** 작업별 에이전트 토큰. 세션을 다시 열어도 남도록 $.store에 둔다(에이전트 id → 토큰, 못 쟀으면 null) */
export type TaskTokens = Record<string, Record<string, number | null>>

/** 숫자로만 정한 경고. 스케줄러(long-stage)와 이 세션의 관측(stall·repeat-fail·ghost)에서 온다. */
export type Anomaly = { kind: 'long-stage' | 'stall' | 'repeat-fail' | 'ghost'; task: string | null; agentId: string | null; text: string }

/** 스냅샷을 읽은 결과. 읽기에 실패하면 이전 스냅샷을 두고 error·stale을 표시한다. */
export type SnapMeta = {
  root: string | null
  stateDir: string | null
  readAtMs: number
  /** 마지막으로 파일 변화를 확인한 시각. 바뀐 것이 없어도 틱마다 갱신해 화면에 '실시간'을 보인다 */
  checkedMs: number
  mtimeMs: number
  error: string | null
  stale: boolean
}

/** 이 세션에서 실제로 관측한 에이전트 실행(agent.spawn 이후). task가 null이면 marker가 없어 미연결이다. */
export type Run = {
  agentId: string
  role: string
  description: string
  task: string | null
  pool: string | null
  attempt: number | null
  /** idle: 일을 마치고 메시지를 기다리는 팀원(다시 일을 받으면 running) */
  status: 'running' | 'idle' | 'completed' | 'failed' | 'stopped'
  startedMs: number
  endedMs: number | null
  tools: number
  lastTool: string
  errors: number
  /** 마지막으로 도구를 부른 시각(없으면 시작 시각). 멈춤 감지에 쓴다 */
  lastActiveMs: number
  /** 끝에서부터 연속으로 실패·거부된 도구 호출 수 */
  failStreak: number
  tokens: number | null
  cwd: string
}

export type Activity = { atMs: number; level: 'info' | 'warn' | 'error'; source: 'mod' | 'agent' | 'tool'; task: string | null; text: string }

export type Tab = 'overview' | 'tasks' | 'agents' | 'decisions' | 'git' | 'activity' | 'commands'
export type TaskMode = 'list' | 'timeline'
export type Layout = 'auto' | 'mini' | 'compact' | 'wide'
export type QueueFilter = 'all' | 'active' | 'waiting' | 'blocked' | 'done'

export type View = {
  tab: Tab
  layout: Layout | null
  /** 색(4-15). null이면 설정값 */
  palette: 'theme' | 'night' | null
  filter: QueueFilter
  taskMode: TaskMode
  expanded: string | null
  errorsOnly: boolean
  /** 에이전트 탭에서 대기·끝난 에이전트를 펼칠지 */
  showDone: boolean
  /** 작업 탭에서 완료·취소 작업을 펼칠지 */
  showDoneTasks: boolean
  /** 보내기 전 확인을 기다리는 미리보기 수정 요청(승인 대신 잘못 쓰는 일을 막는다) */
  pendingRevise: { task: string; version: number; text: string } | null
  lastAction: string | null
}

export type Usage = { costUsd: number | null; contextPct: number | null }

/** Pane 안에서 연 설계·조사 문서(읽기만). */
export type DocView = { task: string; path: string; lines: string[]; more?: number; error: string | null }

declare module 'claude-code' {
  interface PluginState {
    'asterbraid-dashboard': {
      snap: Shaped<Snapshot | null>
      meta: Shaped<SnapMeta>
      runs: Shaped<Run[]>
      activity: Shaped<Activity[]>
      view: Shaped<View>
      usage: Shaped<Usage>
      doc: Shaped<DocView | null>
    }
  }
}
