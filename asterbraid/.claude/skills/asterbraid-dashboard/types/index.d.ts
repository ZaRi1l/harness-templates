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
  /** 조사·설계 노트 경로(sched done --notes) */
  notes?: string[]
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
}

export type Snapshot = {
  schema: string
  schema_version: number
  generated_at: string
  generated_ms: number
  project: { root: string; name: string; state_dir: string }
  integration: { branch: string | null; head: string | null }
  paused: { at: string; reason: string } | null
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
  filter: QueueFilter
  taskMode: TaskMode
  expanded: string | null
  errorsOnly: boolean
  /** 에이전트 탭에서 대기·끝난 에이전트를 펼칠지 */
  showDone: boolean
  lastAction: string | null
}

export type Usage = { costUsd: number | null; contextPct: number | null }

declare module 'claude-code' {
  interface PluginState {
    'asterbraid-dashboard': {
      snap: Shaped<Snapshot | null>
      meta: Shaped<SnapMeta>
      runs: Shaped<Run[]>
      activity: Shaped<Activity[]>
      view: Shaped<View>
      usage: Shaped<Usage>
    }
  }
}
