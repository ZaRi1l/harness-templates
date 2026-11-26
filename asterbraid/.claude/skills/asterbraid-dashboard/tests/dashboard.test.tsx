import type { On } from 'claude-code'
import { expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'

import * as C from '../hooks/core'
import type { Run, Snapshot } from '../types'

const ROOT = '/repo'
const STATE = '/repo/.git/claude-auto'
const SHA1 = 'a'.repeat(64)
const SHA2 = 'b'.repeat(64)

const task = (over: Partial<Snapshot['tasks'][number]>): Snapshot['tasks'][number] => ({
  id: 'T', title: '작업', state: 'READY', priority: 0, risk: 'medium', profile: 'standard', paths: ['src/'], forbid: [],
  deps: [], criteria: '테스트 통과', agent: null, attempts: 0, branch: null, worktree: null, base: null, head: null,
  retries: 0, reason: '', created: '2026-11-24T10:00:00+09:00', updated: '2026-11-24T10:00:00+09:00', completed: null,
  cleaned: false, pool: 'implement', runnable: true, waiting: null, decisions_open: [],
  evidence: { valid: null, required: 1, items: [] },
  preview: { required: false, versions: [], approved: null, feedback: [] },
  timeline: [{ state: 'READY', start: '2026-11-24T10:00:00+09:00', end: null }],
  ...over,
})

const SNAP: Snapshot = {
  schema: 'autonomous-dashboard', schema_version: 1, generated_at: '2026-11-24T10:05:00+09:00', generated_ms: 0,
  project: { root: ROOT, name: 'repo', state_dir: STATE },
  integration: { branch: 'integration', head: 'c0ffee1234' },
  paused: null, total: 4, completed: 1,
  counts: { COMPLETED: 1, IMPLEMENTING: 1, AWAITING_APPROVAL: 1, READY: 1 },
  slots: { research: { used: 0, limit: 2 }, design: { used: 0, limit: 1 }, preview: { used: 0, limit: 2 }, implement: { used: 1, limit: 3 }, verify: { queued: 0, limit: 2 } },
  tasks: [
    task({ id: 'API', title: '로그인 API를 아주 길게 설명하는 한국어 작업 제목입니다', state: 'IMPLEMENTING', agent: 'impl-1', attempts: 1, runnable: false, pool: null }),
    task({
      id: 'UI', title: '로그인 화면', state: 'AWAITING_APPROVAL', runnable: false, pool: null,
      preview: {
        required: true, approved: null, feedback: [{ at: '', on: 1, text: '버튼을 크게' }],
        versions: [
          { n: 1, file: `${STATE}/previews/UI/v1.html`, at: '', summary: '첫 안', sha256: SHA1 },
          { n: 2, file: `${STATE}/previews/UI/v2.html`, at: '', summary: '버튼 키움', sha256: SHA2 },
        ],
      },
    }),
    task({ id: 'DOC', title: '문서', state: 'READY', decisions_open: ['D-1'], runnable: false, waiting: "사용자 결정 대기: ['D-1']" }),
    task({ id: 'OLD', title: '끝난 작업', state: 'COMPLETED' }),
  ],
  decisions: [{
    id: 'D-1', title: '문서 형식', question: '어느 형식?', kind: 'ux', tasks: ['DOC'],
    options: [{ key: 'A', label: 'Markdown', pros: '가볍다', cons: '' }, { key: 'B', label: 'HTML', pros: '', cons: '무겁다' }],
    recommend: 'A', why: '저장소에서 바로 읽힌다', state: 'OPEN', created: '', choice: null, note: null, by: null, answered_at: null,
  }],
  notes: [{ at: '', kind: 'strategy', text: 'API 먼저 통합한다', task: 'API' }],
  events: [{ task: 'API', at: '2026-11-24T10:01:00+09:00', from: 'READY', to: 'IMPLEMENTING', reason: '' }],
  integration_log: [{ time: '', status: 'MERGED', task: 'OLD', message: "'integration' a -> b" }],
  metrics: { implement_peak_parallel: 1, implement_slot_limit: 3 },
  anomalies: [],
  dashboard_html: `${STATE}/previews/index.html`,
}

const run = (over: Partial<Run>): Run => ({
  agentId: 'a1', role: 'implementer', description: '', task: null, pool: null, attempt: null, status: 'running',
  startedMs: 0, endedMs: null, tools: 0, lastTool: '', errors: 0, lastActiveMs: 0, failStreak: 0, tokens: null, cwd: '', ...over,
})

// ---------------------------------------------------------------- 순수 함수

test('경고: 멈춤·연속 실패·유령 슬롯·긴 단계를 숫자로만 정한다', async () => {
  const MIN = 60_000
  const snap = { ...SNAP, anomalies: [{ kind: 'long-stage' as const, task: 'API', state: 'IMPLEMENTING', seconds: 1800, median_s: 120, text: 'API IMPLEMENTING 30분째' }] }
  const runs = [
    run({ agentId: 'idle', task: 'API', pool: 'implement', lastActiveMs: 0 }),
    run({ agentId: 'busy', lastActiveMs: 19 * MIN, failStreak: 3, lastTool: 'Bash' }),
    run({ agentId: 'done', status: 'completed', lastActiveMs: 0 }),
  ]
  const kinds = C.anomalies(snap, runs, 20 * MIN, 10 * MIN).map(a => `${a.kind}:${a.task ?? a.agentId}`)
  expect(kinds).toEqual(['long-stage:API', 'stall:API', 'repeat-fail:busy'])
  expect(C.anomalies(SNAP, [], 0, 10 * MIN).map(a => a.kind)).toEqual(['ghost']) // API 슬롯, 실행 없음
  expect(C.statusText(snap, C.EMPTY_META, runs, C.anomalies(snap, runs, 20 * MIN, 10 * MIN))).toContain('경고 3')
})

test('연속 실패는 성공 한 번에 끊긴다', async () => {
  let b = C.addCall(undefined, 'Bash', true, 1)
  b = C.addCall(b, 'Bash', true, 2)
  expect(C.applyBatch(run({ failStreak: 2 }), b).failStreak).toBe(4)
  b = C.addCall(b, 'Read a.py', false, 3)
  b = C.addCall(b, 'Bash', true, 4)
  const r = C.applyBatch(run({ failStreak: 2 }), b)
  expect([r.failStreak, r.tools, r.errors, r.lastActiveMs, r.lastTool]).toEqual([1, 4, 3, 4, 'Bash'])
})

test('marker만으로 작업을 잇고, 없으면 잇지 않는다', async () => {
  expect(C.parseMarker('[auto-task id=UI-1 pool=implement attempt=2]\n구현해')).toEqual({ task: 'UI-1', pool: 'implement', attempt: 2 })
  expect(C.parseMarker('implementer로 UI-1을 구현해')).toBe(null)
  expect(C.parseMarker('[auto-task id=../x pool=implement attempt=1]')).toBe(null)
})

test('명령 해석: 승인·수정 요청은 사용자가 본 버전이 있어야 한다', async () => {
  expect(C.parseCommand('')).toEqual({ kind: 'open' })
  expect(C.parseCommand('queue')).toEqual({ kind: 'tab', tab: 'tasks', mode: 'list' })
  expect(C.parseCommand('timeline')).toEqual({ kind: 'tab', tab: 'tasks', mode: 'timeline' })
  expect(C.parseCommand('metrics')).toEqual({ kind: 'tab', tab: 'git', mode: null })
  expect(C.parseCommand('status')).toEqual({ kind: 'tab', tab: 'overview', mode: null })
  expect(C.parseCommand('commands')).toEqual({ kind: 'tab', tab: 'commands', mode: null })
  expect(C.parseCommand('preview UI v2')).toEqual({ kind: 'preview', task: 'UI', version: 2 })
  expect(C.parseCommand('preview')).toEqual({ kind: 'preview', task: null, version: null })
  expect(C.parseCommand('approve UI').kind).toBe('error')
  expect(C.parseCommand('revise UI v2 버튼 색 바꿔')).toEqual({ kind: 'revise', task: 'UI', version: 2, feedback: '버튼 색 바꿔' })
  expect(C.parseCommand('limit implement 99').kind).toBe('error')
  expect(C.parseCommand('rm -rf /').kind).toBe('error')
})

test('sched 인수: 해시를 함께 보내고, 스냅샷에 없는 대상은 거부한다', async () => {
  expect(C.schedArgs({ kind: 'approve', task: 'UI', version: 2 }, SNAP)).toEqual({
    args: ['approve', 'UI', '--version', '2', '--sha', SHA2.slice(0, 16), '--by', 'tui'],
  })
  const stale = C.schedArgs({ kind: 'approve', task: 'UI', version: 1 }, SNAP) // v2가 나온 뒤의 v1 승인
  expect('error' in stale && stale.error.includes('최신')).toBe(true)
  expect('error' in C.schedArgs({ kind: 'approve', task: 'UI', version: 3 }, SNAP)).toBe(true)
  expect('error' in C.schedArgs({ kind: 'approve', task: 'API', version: 1 }, SNAP)).toBe(true)
  expect('error' in C.schedArgs({ kind: 'decide', id: 'D-1', choice: 'Z', note: '' }, SNAP)).toBe(true)
  expect(C.schedArgs({ kind: 'decide', id: 'D-1', choice: 'B', note: '' }, SNAP)).toEqual({
    args: ['decide', 'answer', 'D-1', '--choice=B', '--by', 'tui'],
  })
  // 피드백에 셸 문자가 있어도 인수 하나로만 가고, -로 시작해도 옵션으로 읽히지 않는다
  const r = C.schedArgs({ kind: 'revise', task: 'UI', version: 2, feedback: '"; rm -rf / #' }, SNAP)
  expect('args' in r && r.args[4]).toBe('--feedback="; rm -rf / #')
  const dash = C.schedArgs({ kind: 'revise', task: 'UI', version: 2, feedback: '-여백 줄이기' }, SNAP)
  expect('args' in dash && dash.args[4]).toBe('--feedback=-여백 줄이기')
  expect(C.schedArgs({ kind: 'pause', reason: '-점검' }, SNAP)).toEqual({ args: ['pause', '--reason=-점검'] })
})

test('HTML이 아닌 미리보기는 열지 않는다', async () => {
  const bat = { ...SNAP, tasks: SNAP.tasks.map(t => (t.id === 'UI' ? { ...t, preview: { ...t.preview, versions: [{ n: 1, file: `${STATE}/previews/UI/v1.bat`, at: '', sha256: SHA1 }] } } : t)) }
  const r = C.previewTarget(bat, 'UI', 1)
  expect('error' in r && r.error.includes('.html')).toBe(true)
})

test('한글은 두 칸으로 재고 폭에 맞게 자른다', async () => {
  expect(C.strWidth('가나a')).toBe(5)
  expect(C.fit('가나다라', 5)).toBe('가나…')
  expect(C.strWidth(C.pad('한글', 6))).toBe(6)
  const l = C.fitLine([{ text: '작업 ' }, { text: '아주 긴 제목입니다' }], 10)
  expect(C.lineWidth(l) <= 10).toBe(true)
})

test('레이아웃 자동 선택', async () => {
  expect(C.pickLayout('auto', 'inline', 200)).toBe('mini')
  expect(C.pickLayout('auto', 'dock', 120)).toBe('wide')
  expect(C.pickLayout('auto', 'dock', 60)).toBe('compact')
  expect(C.pickLayout('compact', 'inline', 200)).toBe('compact')
})

test('비밀값 마스킹과 도구 요약', async () => {
  expect(C.redact('curl -H "Authorization: Bearer abcdefghijklmnop" sk-abcdefghijklmnopqrstu')).toBe('curl -H "Authorization: Bearer ***" sk-***')
  expect(C.redact('API_KEY=abc123 password: hunter2')).toBe('API_KEY=*** password: ***')
  expect(C.toolSummary('Bash', { command: 'echo token=abc' }, 'names')).toBe('Bash')
  expect(C.toolSummary('Bash', { command: 'echo token=abc' }, 'masked')).toBe('Bash echo token=***')
  expect(C.toolSummary('Edit', { file_path: '/repo/src/secret/a.py' }, 'names')).toBe('Edit a.py')
})

test('스냅샷 검사: 손상·스키마 불일치를 구분한다', async () => {
  expect('snap' in C.parseSnapshot(JSON.stringify(SNAP))).toBe(true)
  expect(C.parseSnapshot('{').hasOwnProperty('error')).toBe(true)
  const r = C.parseSnapshot(JSON.stringify({ ...SNAP, schema_version: 2 }))
  expect('error' in r && r.error.includes('v2')).toBe(true)
})

test('미리보기 경로와 브라우저 명령', async () => {
  expect(C.previewTarget(SNAP, 'UI', null)).toEqual({ file: `${STATE}/previews/UI/v2.html`, label: 'UI v2' })
  expect(C.previewTarget(SNAP, null, null)).toEqual({ file: `${STATE}/previews/index.html`, label: '미리보기 대시보드' })
  expect('error' in C.previewTarget(SNAP, 'API', null)).toBe(true)
  expect(C.isInside('C:\\R\\.git\\claude-auto\\previews\\UI\\v1.html', 'c:/r/.git/claude-auto/previews', true)).toBe(true)
  expect(C.isInside('/repo/.git/claude-auto/previews-evil/x.html', '/repo/.git/claude-auto/previews', false)).toBe(false)
  // PATH에 C:\Windows가 없는 설정에서도 열리게 절대 경로를 다음 후보로 둔다
  expect(C.openerArgv('Windows_NT', 'C:/a b/v1.html')).toEqual([['explorer.exe', 'C:\\a b\\v1.html'], ['C:\\Windows\\explorer.exe', 'C:\\a b\\v1.html']])
  expect(C.openerArgv('Linux', '/a/v1.html')).toEqual([['xdg-open', '/a/v1.html']])
})

test('슬롯 점유와 실제 실행을 구분한다', async () => {
  expect(C.unobservedHolders(SNAP, []).map(t => t.id)).toEqual(['API'])
  expect(C.unobservedHolders(SNAP, [run({ task: 'API', pool: 'implement' })])).toEqual([])
  expect(C.statusText(SNAP, C.EMPTY_META, [])).toBe('자율 완료 1/4 · 실행 0/배정 1 · 답 필요 2')
})

test('대기열 필터와 결정 대기 표시', async () => {
  const waiting = C.queueLines(SNAP, 'waiting', null, 80).filter(r => r.id).map(r => r.id)
  expect(waiting).toEqual(['UI', 'DOC'])
  const doc = C.queueLines(SNAP, 'all', 'DOC', 80).map(r => r.line.map(s => s.text).join(''))
  expect(doc.some(l => l.includes('결정 대기 D-1'))).toBe(true)
  expect(doc.some(l => l.includes('완료 기준'))).toBe(true)
})

test('새 승인·결정 대기만 알린다', async () => {
  const before = { ...SNAP, tasks: SNAP.tasks.map(t => (t.id === 'UI' ? { ...t, state: 'PREVIEWING' as const } : t)), decisions: [] }
  expect(C.changes(before, SNAP)).toEqual(['UI 미리보기 검토 대기', 'D-1 결정 필요: 문서 형식'])
  expect(C.changes(SNAP, SNAP)).toEqual([])
})

// ---------------------------------------------------------------- 엔진 위에서

type Calls = { argv: string[][]; live: { snap: Snapshot | string; mtime: number }; clock: ReturnType<typeof mock.clock>; agents: { id: string; status: string; taskStatus: string }[] }

// 엔진은 realPath를 실행 중인 OS의 표기로 돌려준다(Windows면 D:\repo\...). 비교할 때는 / 로 맞춘다.
const slash = (a: string[] | undefined) => a?.map(x => x.replace(/\\/g, '/').replace(/^[A-Za-z]:/, ''))

const engine = (on: On, snap: Snapshot | string = SNAP, opts: { gitFails?: boolean; sysname?: string; noPathExplorer?: boolean; teammateOnly?: boolean; noSnapshotYet?: boolean } = {}): Calls => {
  const calls: Calls = { argv: [], live: { snap, mtime: 1 }, clock: mock.clock(on), agents: [] }
  on('command.register', () => ({ value: { command: 'asterbraid' } }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('session.machine', () => ({ value: { sysname: opts.sysname ?? 'Linux', release: '6', machine: 'x86_64', packageManagers: [], runtimes: [] } }))
  on('session.usage', () => ({ value: { startedAt: 0, context: { window: 200000 }, rateLimits: [] } }))
  on('agent.list', () => ({ value: calls.agents.map(x => ({ ...x, description: '', type: 'teammate', kind: 'in_process_teammate' })) as never }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.close', () => ({ value: undefined }))
  on('ui.status', () => ({ value: undefined }))
  on('ui.toast', () => ({ value: undefined }))
  const mem = new Map<string, unknown>() // $.store 흉내(세션 너머 값)
  let made = false // noSnapshotYet: sched snapshot을 부르기 전에는 상태·스냅샷 파일이 없다
  on('store.get', (_, e) => ({ value: mem.get(e.key) }))
  on('store.set', (_, e) => {
    mem.set(e.key, JSON.parse(JSON.stringify(e.value)))
    return { value: undefined }
  })
  on('process.run', (_, e) => {
    calls.argv.push([...e.argv])
    if (e.argv.includes('snapshot')) made = true
    if (opts.gitFails && e.argv[0] === 'git') throw new Error('spawn git ENOENT')
    if (opts.noPathExplorer && e.argv[0] === 'explorer.exe') throw new Error('spawn explorer.exe ENOENT')
    const out = e.argv[0] === 'git' ? `${ROOT}/.git\n` : '{}'
    return { value: { exitCode: 0, stdout: out, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('fs.stat', (_, e) => {
    if (e.path.endsWith('dashboard.error.json')) throw new Error('ENOENT')
    if (opts.noSnapshotYet && !made && /(dashboard|state)\.json$/.test(e.path)) throw new Error('ENOENT')
    const kind = e.path.endsWith('.py') || e.path.endsWith('.json') || e.path.endsWith('.html') ? 'file' : 'dir'
    return { value: { kind, size: 1, mtimeMs: calls.live.mtime, isLink: false, ...(e.resolve ? { realPath: e.path } : {}) } }
  })
  on('fs.read', (_, e) => ({ value: e.path.endsWith('.md') ? '# 설계\n계약 본문' : typeof calls.live.snap === 'string' ? calls.live.snap : JSON.stringify(calls.live.snap) }))
  let spawned = 0
  on('agent.spawn', () => (opts.teammateOnly ? { model: 'sonnet', teammateId: `mate-${++spawned}@team` } : { model: 'sonnet', agentId: `ag-${++spawned}` }))
  return calls
}

const PANE = {
  plugin: 'asterbraid-dashboard', component: 'Pane', requestId: 'asterbraid',
  props: { title: 'AsterBraid', isFocused: false, bodyColumns: 72, placement: 'dock', scroll: { offset: 0, bodyRows: 30 }, view: {} },
} as const

const start = ($: Engine) => $.session.start({ cwd: ROOT, surface: 'terminal', isInteractive: true })
const command = ($: Engine, args: string) =>
  $.command.run({ command: 'asterbraid', args, origin: { kind: 'composer' }, presentation: { isFullscreen: true, columns: 160 } })
const spawn = ($: Engine, prompt: string, subagentType: string, description: string) =>
  $.agent.spawn({
    prompt, subagentType, description, tool_use_id: `tu-${description}`, provider: { plugin: 'engine', tier: 'core' },
    parentModel: 'opus', background: false, fork: false,
  })

test('세션을 시작하면 스냅샷을 읽어 모든 표면에 그린다', async ($, on) => {
  engine(on)
  await start($)
  for (const surface of ['terminal', 'desktop', 'vscode', 'mobile'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    expect((await ui.find({ type: 'Text', text: /1\/4/ }))?.text).toContain('작업 수 기준')
    expect(await ui.find({ type: 'Text', text: /사용자 차례/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /실행 미확인 1/ })).toBeDefined()
    await ui.unmount()
  }
})

test('git을 실행하지 못해도 .git을 파일로 찾아 그린다', async ($, on) => {
  engine(on, SNAP, { gitFails: true })
  await start($)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /1\/4/ }))?.text).toContain('작업 수 기준')
  await ui.unmount()
})

test('경로 도우미: 드라이브·상위 폴더·worktree의 .git 파일', async () => {
  expect(C.joinPath('D:\\a\\b', '..\\c')).toBe('D:/a/c')
  expect(C.joinPath('/x/y', '/z')).toBe('/z')
  expect(C.parentDir('D:/a')).toBe('D:/')
  expect(C.parentDir('D:/')).toBe(null)
  expect(C.parentDir('/')).toBe(null)
  expect(C.gitdirOf('gitdir: ../main/.git/worktrees/w1\n', 'D:/p/w1')).toBe('D:/p/main/.git/worktrees/w1')
  expect(C.joinPath('D:/p/main/.git/worktrees/w1', '../..')).toBe('D:/p/main/.git')
})

test('좁은 inline 자리에서는 mini 요약만 그린다', async ($, on) => {
  engine(on)
  await start($)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal', props: { ...PANE.props, placement: 'inline', bodyColumns: 40 } })
  expect((await ui.find({ type: 'Text', text: /✦ AsterBraid/ }))?.text).toContain('1/4')
  expect(await ui.find({ key: 'tab-tasks' })).toBeUndefined()
  await ui.unmount()
})

test('marker가 있는 에이전트만 작업에 연결한다', async ($, on) => {
  engine(on)
  await start($)
  await spawn($, '[auto-task id=API pool=implement attempt=1]\n구현', 'implementer', 'API 구현')
  await spawn($, '아무 지시', 'Explore', '탐색')
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'tab-agents' })
  expect(await ui.find({ type: 'Text', text: /implementer\s+구현/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /미연결/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /실행이 관측되지 않은/ })).toBeUndefined()
  await ui.unmount()
})

test('승인 명령은 본 버전의 해시와 함께 sched.py를 argv로 실행한다', async ($, on) => {
  const calls = engine(on)
  await start($)
  const r = await command($, 'approve UI v2')
  expect(r.text).toContain('완료')
  const sched = slash(calls.argv.find(a => a.includes('approve')))
  expect(sched).toEqual(['uv', 'run', '--no-project', '--offline', 'python', `${ROOT}/.claude/hooks/sched.py`, 'approve', 'UI', '--version', '2', '--sha', SHA2.slice(0, 16), '--by', 'tui'])
  expect((await command($, 'approve UI v1')).text).toContain('최신') // 거절된 옛 버전은 sched.py까지 가지 않는다
})

test('Pane의 결정 버튼은 사용자가 누른 선택지만 기록한다', async ($, on) => {
  const calls = engine(on)
  await start($)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'tab-decisions' })
  expect(calls.argv.some(a => a.includes('decide'))).toBe(false) // 그리기만으로 기록하지 않는다
  await ui.press({ key: 'pick-D-1-B' })
  expect(calls.argv.find(a => a.includes('decide'))?.slice(-6)).toEqual(['decide', 'answer', 'D-1', '--choice=B', '--by', 'tui'])
  await ui.unmount()
})

test('preview 명령은 등록된 미리보기 폴더 안의 파일만 브라우저로 연다', async ($, on) => {
  const evil = { ...SNAP, tasks: SNAP.tasks.map(t => (t.id === 'UI' ? { ...t, preview: { ...t.preview, versions: [{ n: 1, file: '/etc/evil.html', at: '', sha256: SHA1 }] } } : t)) }
  const calls = engine(on, evil)
  await start($)
  const r = await command($, 'preview UI v1')
  expect(r.text).toContain('밖')
  expect(calls.argv.some(a => a[0] === 'xdg-open')).toBe(false)
})

test('preview 명령: 대시보드를 새로 만들고 기본 브라우저로 연다', async ($, on) => {
  const calls = engine(on)
  await start($)
  const r = await command($, 'preview')
  expect(r.text).toContain('기본 브라우저')
  expect(calls.argv.some(a => a.includes('dashboard'))).toBe(true)
  expect(slash(calls.argv.find(a => a[0] === 'xdg-open'))).toEqual(['xdg-open', `${STATE}/previews/index.html`])
})

test('읽기 전용이면 상태를 바꾸지 않는다', { options: { allowActions: false } }, async ($, on) => {
  const calls = engine(on)
  await start($)
  const r = await command($, 'approve UI v2')
  expect(r.text).toContain('읽기 전용')
  expect(calls.argv.some(a => a.includes('approve'))).toBe(false)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'tab-decisions' })
  expect(await ui.find({ key: 'pick-D-1-A' })).toBeUndefined()
  await ui.unmount()
})

test('손상된 스냅샷은 작업 상태로 바꾸지 않고 오류로 보인다', async ($, on) => {
  engine(on, '{broken')
  await start($)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /손상/ }))?.text).toContain('JSON')
  await ui.unmount()
})

test('스냅샷 파일이 바뀌면 명령 없이 다음 틱에 다시 그린다', async ($, on) => {
  const calls = engine(on)
  await start($)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const live = async () => (await ui.find({ type: 'Text', text: /실시간/ }))?.text
  const before = await live()
  expect(before).toBeDefined()
  calls.live.snap = { ...SNAP, completed: 2, tasks: SNAP.tasks.map(t => (t.id === 'API' ? { ...t, state: 'COMPLETED' as const } : t)) }
  calls.live.mtime = 2
  await calls.clock.advance(2_000)
  expect((await ui.find({ type: 'Text', text: /2\/4/ }))).toBeDefined()
  expect(await live()).not.toBe(before) // 바뀐 것이 없어도 확인할 때마다 표시가 움직인다
  await ui.unmount()
})

test('명령 탭: 버튼만으로 일시 중지·한도·우선순위를 바꾼다', async ($, on) => {
  const calls = engine(on)
  await start($)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'tab-commands' })
  expect(calls.argv.some(a => a.includes('pause'))).toBe(false) // 탭을 여는 것만으로는 바꾸지 않는다
  await ui.press({ key: 'cmd-pause' })
  expect(calls.argv.find(a => a.includes('pause'))?.slice(-2)).toEqual(['pause', '--reason=TUI에서 일시 중지'])
  await ui.press({ key: 'inc-implement' })
  expect(calls.argv.find(a => a.includes('limit'))?.slice(-3)).toEqual(['limit', 'implement', '4'])
  expect(await ui.find({ key: 'pri-up-DOC' })).toBeUndefined() // 우선순위는 작업 탭에서 바꾼다
  await ui.press({ key: 'tab-tasks' })
  await ui.press({ key: 'q-DOC' })
  await ui.press({ key: 'pri-up-DOC' })
  expect(calls.argv.find(a => a.includes('priority'))?.slice(-3)).toEqual(['priority', 'DOC', '1'])
  await ui.unmount()
})

test('긴 설명은 낱말 단위로 줄을 나누고 넘치면 …로 끝낸다', async () => {
  expect(C.wrapText('settings의 PATH에 git이 없다', 17, 3)).toEqual(['settings의 PATH에', 'git이 없다'])
  expect(C.wrapText('abcdefghij', 4, 5)).toEqual(['abcd', 'efgh', 'ij'])
  const cut = C.wrapText('가 나 다 라 마 바 사 아', 6, 2)
  expect(cut.length).toBe(2)
  expect(cut[1]?.endsWith('…')).toBe(true)
})

test('보류 해제: 명령과 sched 인수, BLOCKED가 아니면 거부한다', async () => {
  expect(C.parseCommand('unblock CORE')).toEqual({ kind: 'unblock', task: 'CORE' })
  expect(C.parseCommand('unblock').kind).toBe('error')
  const blocked = { ...SNAP, tasks: SNAP.tasks.map(t => (t.id === 'API' ? { ...t, state: 'BLOCKED' as const, reason: '환경' } : t)) }
  expect(C.schedArgs({ kind: 'unblock', task: 'API' }, blocked)).toEqual({ args: ['unblock', 'API', '--reason=TUI에서 보류 해제'] })
  expect('error' in C.schedArgs({ kind: 'unblock', task: 'API' }, SNAP)).toBe(true)
})

test('작업 탭: 보류된 작업을 펼치면 보류 해제 버튼이 있다', async ($, on) => {
  const blocked = { ...SNAP, counts: { ...SNAP.counts, BLOCKED: 1 }, tasks: SNAP.tasks.map(t => (t.id === 'API' ? { ...t, state: 'BLOCKED' as const, reason: '환경' } : t)) }
  const calls = engine(on, blocked)
  await start($)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'tab-tasks' })
  expect(await ui.find({ key: 'unblock-API' })).toBeUndefined() // 펼치기 전에는 없다
  await ui.press({ key: 'q-API' })
  await ui.press({ key: 'unblock-API' })
  expect(calls.argv.find(a => a.includes('unblock'))?.slice(-3)).toEqual(['unblock', 'API', '--reason=TUI에서 보류 해제'])
  await ui.unmount()
})

test('탭 이름에 단축키 숫자를 겹쳐 쓰지 않는다', async ($, on) => {
  engine(on)
  await start($)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const texts = (await ui.findAll({ type: 'Text', text: /현황/ })).map(x => x.text)
  expect(texts.some(t => /\d\s*현황/.test(t))).toBe(false)
  await ui.unmount()
})

test('Windows: PATH에 explorer.exe가 없어도 절대 경로로 열고, 실패 경로는 자르지 않는다', async ($, on) => {
  const calls = engine(on, SNAP, { sysname: 'Windows_NT', noPathExplorer: true })
  await start($)
  const r = await command($, 'preview UI v2')
  expect(r.text).toContain('기본 브라우저')
  expect(calls.argv.some(a => a[0] === 'C:\\Windows\\explorer.exe')).toBe(true)
})

test('세션 시작 없이 다시 불러와져도(리로드) 다른 Hook에서 갱신 타이머를 다시 켠다', async ($, on) => {
  const calls = engine(on)
  // 리로드 뒤처럼: 저장된 상태로 Pane을 그리지만 session.start는 다시 오지 않는다
  await $.command.run({ command: 'asterbraid', args: 'refresh', origin: { kind: 'composer' }, presentation: { isFullscreen: true, columns: 160 } })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const live = async () => (await ui.find({ type: 'Text', text: /실시간/ }))?.text
  const before = await live()
  expect(before).toBeDefined()
  await calls.clock.advance(2_000)
  expect(await live()).not.toBe(before)
  await ui.unmount()
})

test('에이전트 목록: 일을 마친 팀원(idle)은 실행으로 세지 않고, 목록에 없는 것은 그대로 둔다', async () => {
  const runs = [run({ agentId: 'idle-mate' }), run({ agentId: 'busy' }), run({ agentId: 'gone' }), run({ agentId: 'done' })]
  const agents = [
    { id: 'idle-mate', status: 'idle', taskStatus: 'running', tokenCount: 1200 },
    { id: 'busy', status: 'running', taskStatus: 'running' },
    { id: 'done', status: 'completed', taskStatus: 'completed' },
  ] as const
  const next = C.applyAgentList(runs, agents, 50)
  expect(next.map(r => `${r.agentId}:${r.status}`)).toEqual(['idle-mate:idle', 'busy:running', 'gone:running', 'done:completed'])
  expect(next[0]?.tokens).toBe(1200)
  expect(C.runningRuns(next).map(r => r.agentId)).toEqual(['busy', 'gone'])
  // idle 팀원은 멈춤 경고 대상이 아니다
  expect(C.anomalies(null, next, 60 * 60_000, 10 * 60_000).map(a => a.agentId)).toEqual(['busy', 'gone'])
  // 다시 일을 받으면 실행으로 돌아온다
  expect(C.applyAgentList(next, [{ id: 'idle-mate', status: 'running', taskStatus: 'running' }], 60)[0]?.status).toBe('running')
})

test('목록에 없는 에이전트(예: 격리 worktree 실행)를 끝난 것으로 바꾸지 않는다', async ($, on) => {
  const calls = engine(on) // agent.list는 빈 목록을 돌려준다
  await start($)
  await spawn($, '[auto-task id=API pool=implement attempt=1]\n구현', 'implementer', 'API 구현')
  await calls.clock.advance(6_000) // 세 틱: 에이전트 목록을 읽는 틱이 지나간다
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /실행 미확인/ })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /implementer · / })).toBeDefined()
  await ui.unmount()
})

test('일을 마친 뒤(idle) 목록에서 사라진 팀원은 끝난 것으로 본다', async () => {
  const runs = [run({ agentId: 'mate', status: 'idle', endedMs: 10 }), run({ agentId: 'busy' })]
  const next = C.applyAgentList(runs, [], 50)
  expect(next.map(r => `${r.agentId}:${r.status}`)).toEqual(['mate:completed', 'busy:running'])
  expect(next[0]?.endedMs).toBe(10)
})

test('같은 marker로 띄운 verifier 두 개가 모두 그려진다', async ($, on) => {
  engine(on)
  await start($)
  await spawn($, '[auto-task id=API pool=verify attempt=1]\n정확성 검증', 'verifier', '정확성 검증')
  await spawn($, '[auto-task id=API pool=verify attempt=1]\n보안 검증', 'verifier', '보안 검증')
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'tab-agents' })
  const heads = await ui.findAll({ type: 'Text', text: /verifier\s+검증/ })
  expect(heads.length).toBe(2)
  await ui.unmount()
})

test('agentId 없이 팀원 주소만 돌려주는 실행도 기록한다', async ($, on) => {
  engine(on, SNAP, { teammateOnly: true })
  await start($)
  await spawn($, '[auto-task id=API pool=verify attempt=1]\n보안 검증', 'verifier', '보안 검증')
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'tab-agents' })
  expect(await ui.find({ type: 'Text', text: /verifier\s+검증/ })).toBeDefined()
  await ui.unmount()
})

test('대기열: 펼친 작업에 설계 깊이와 노트를 보인다', async () => {
  const snap = { ...SNAP, tasks: SNAP.tasks.map(t => (t.id === 'API' ? { ...t, design_depth: 'standard', notes: ['notes/API/design.md'] } : t)) }
  const lines = C.queueLines(snap, 'all', 'API', 120).map(r => r.line.map(s => s.text).join(''))
  expect(lines.some(l => l.includes('설계 standard · 노트 notes/API/design.md'))).toBe(true)
  expect(C.queueLines(SNAP, 'all', 'API', 120).some(r => r.line.map(s => s.text).join('').includes('설계 '))).toBe(false)
})

test('에이전트 탭: 작업별로 묶고, 대기 팀원은 작업 아래에 보이며 끝난 것만 접는다', async ($, on) => {
  const calls = engine(on)
  await start($)
  await spawn($, '[auto-task id=API pool=implement attempt=1]\n구현', 'implementer', 'API 구현')
  await spawn($, '[auto-task id=UI pool=preview attempt=1]\n미리보기', 'designer', 'UI 미리보기')
  calls.agents = [{ id: 'ag-1', status: 'running', taskStatus: 'running' }, { id: 'ag-2', status: 'idle', taskStatus: 'running' }]
  await calls.clock.advance(6_000) // 에이전트 목록을 읽는 틱
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'tab-agents' })
  expect(await ui.find({ type: 'Text', text: /실행 중 1/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /implementer\s+구현/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /designer\s+미리보기 \(팀원 대기\)/ })).toBeDefined() // UI가 아직 승인 대기라 다시 쓰일 수 있다
  expect(await ui.find({ type: 'Text', text: /UI\s+승인/ })).toBeDefined()
  await ui.unmount()
})

test('결정 버튼을 연달아 눌러도 sched.py는 한 번만 부른다', async ($, on) => {
  const calls = engine(on)
  await start($)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'tab-decisions' })
  await Promise.all([ui.press({ key: 'pick-D-1-A' }), ui.press({ key: 'pick-D-1-A' })])
  expect(calls.argv.filter(a => a.includes('decide')).length).toBe(1)
  await ui.unmount()
})

test('머리말: 제목 별과 반짝이는 실시간 표시, 별 문자는 한 칸으로 재고 폭을 넘지 않는다', async () => {
  expect([C.charWidth(0x2726), C.charWidth(0x2727)]).toEqual([1, 1]) // ✦ ✧
  const frames = [0, 2000, 4000, 6000].map(t => {
    const head = C.headerLines(SNAP, { ...C.EMPTY_META, checkedMs: t }, [], 0, [], 60, 2000)[0] ?? []
    expect(C.lineWidth(head)).toBeLessThanOrEqual(60)
    expect(head.map(s => s.text).join('')).toContain('✦ ASTERBRAID')
    return head[head.length - 1]?.text.split(' ')[0]
  })
  expect(frames).toEqual(['·', '✧', '✦', '✧'])
})

// ---------------------------------------------------------------- e2e4 묶음 (4-4~4-13, N4, N7)

test('에이전트: 대기 먼저, 끝남은 아래. 작업과 그 작업에 기대는 작업이 끝난 대기 팀원은 끝남이다', async () => {
  const base = { ...SNAP, tasks: [task({ id: 'C', state: 'COMPLETED' }), task({ id: 'X', state: 'IMPLEMENTING', deps: ['C'] }), task({ id: 'Y', state: 'COMPLETED' })] }
  const runs = [
    run({ agentId: 'arch', role: 'architect', task: 'C', pool: 'design', attempt: 1, status: 'idle' }),
    run({ agentId: 'ver', role: 'verifier', task: 'Y', pool: 'verify', attempt: 1, status: 'idle' }),
    run({ agentId: 'old', role: 'implementer', task: 'Y', pool: 'implement', attempt: 1, status: 'completed', endedMs: 1 }),
  ]
  const g = C.doneGroups(base, runs)
  expect(g.idle.map(r => r.agentId)).toEqual(['arch']) // 계약을 쓰는 X가 아직 구현 중이다
  expect(g.ended.map(r => r.agentId).sort()).toEqual(['old', 'ver'])
  const text = C.agentLines(base, runs, 10, null, true).map(l => l.map(s => s.text).join(''))
  expect(text.findIndex(t => t.includes('architect'))).toBeLessThan(text.findIndex(t => t.includes('verifier')))
  expect(text.find(t => t.startsWith('✓ C '))).toContain('X가 이 계약을 쓰는 중') // 완료인데 대기에 남은 이유
  expect(text.some(t => t.includes('architect') && t.includes('(팀원 대기)'))).toBe(true)
  const later = { ...base, tasks: base.tasks.map(t => (t.id === 'X' ? { ...t, state: 'COMPLETED' as const } : t)) }
  expect(C.doneGroups(later, runs).idle).toEqual([])
})

test('에이전트 상한: 끝난 것부터 버리고 실행 중·대기는 남긴다', async () => {
  const runs = [run({ agentId: 'idle', status: 'idle' }), run({ agentId: 'done', status: 'completed' }), run({ agentId: 'live' })]
  expect(C.upsertRun(runs, run({ agentId: 'new' }), 3).map(r => r.agentId)).toEqual(['idle', 'live', 'new'])
  expect(C.upsertRun(runs, run({ agentId: 'new' }), 2).map(r => r.agentId)).toEqual(['live', 'new'])
})

test('회차: 같은 단계를 몇 번째 하는지로 보이고 첫 번째는 숫자를 붙이지 않는다', async () => {
  const snap = { ...SNAP, events: [
    { task: 'UI', at: '', from: 'PENDING', to: 'PREVIEWING', reason: '', pool: 'preview', attempt: 1 },
    { task: 'UI', at: '', from: 'READY', to: 'IMPLEMENTING', reason: '', pool: 'implement', attempt: 3 },
    { task: 'UI', at: '', from: 'RETRY', to: 'IMPLEMENTING', reason: '', pool: 'implement', attempt: 4 },
  ] }
  expect(C.poolRound(snap, run({ task: 'UI', pool: 'implement', attempt: 3 }))).toBe(1)
  expect(C.poolRound(snap, run({ task: 'UI', pool: 'implement', attempt: 4 }))).toBe(2)
  const line = C.agentLines(snap, [run({ task: 'UI', pool: 'implement', attempt: 4 })], 0, null)[2]?.map(s => s.text).join('') ?? ''
  expect(line).toContain('구현 2회차')
})

test('작업 목록: 진행 중을 위에, 완료는 접고, 펼치면 걸린 시간·토큰·작게 표시', async () => {
  const done = task({ id: 'OLD', title: '끝난 작업', state: 'COMPLETED', profile: 'fast', started: '2026-11-24T10:00:00+09:00', completed: '2026-11-24T10:18:00+09:00' })
  const snap = { ...SNAP, tasks: [done, ...SNAP.tasks.filter(t => t.id !== 'OLD')] }
  const folded = C.queueLines(snap, 'all', null, 140).map(r => r.line.map(s => s.text).join(''))
  expect(folded[0]).toContain('API')
  expect(folded.some(t => t.includes('끝난 작업'))).toBe(false)
  expect(folded[folded.length - 1]).toContain('완료 1 (접힘)')
  const tok = C.mergeTaskTokens({}, [run({ agentId: 'i', task: 'OLD', tokens: 120_000 }), run({ agentId: 'v', task: 'OLD', tokens: null })])
  const open = C.queueLines(snap, 'all', null, 160, true, tok).map(r => r.line.map(s => s.text).join(''))
  const row = open.find(t => t.includes('끝난 작업')) ?? ''
  expect(row).toContain('작게')
  expect(row).toContain('⏱ 18m')
  expect(row).toContain('≥120k tok · 에이전트 2') // 못 잰 에이전트가 있어 최소값
  expect(open.findIndex(t => t.includes('완료 1'))).toBeLessThan(open.findIndex(t => t.includes('끝난 작업')))
  expect(C.mergeTaskTokens(tok, [run({ agentId: 'v', task: 'OLD', tokens: 5000 })]).OLD).toEqual({ i: 120_000, v: 5000 })
})

test('머리말: 컨텍스트 %와 자리 비움 표시', async () => {
  const text = (snap: Snapshot, pct: number | null) =>
    C.headerLines(snap, C.EMPTY_META, [], 0, [], 120, 1000, { costUsd: null, contextPct: pct }).map(l => l.map(s => s.text).join('')).join('\n')
  expect(text(SNAP, 42)).toContain('컨텍스트 42%')
  expect(text(SNAP, null)).not.toContain('컨텍스트')
  const hot = C.headerLines(SNAP, C.EMPTY_META, [], 0, [], 120, 1000, { costUsd: null, contextPct: 85 })[2]?.find(s => s.text.includes('컨텍스트'))
  expect(hot?.tone).toBe('warn')
  const away = { ...SNAP, away: { at: '', previews: true, reason: '' } }
  expect(text(away, null)).toContain('☾ 자리 비움 중 · 미리보기 자동 승인')
  const back = { ...SNAP, away: null, away_log: [{ at: '', kind: 'decision' as const, key: 'D-1', text: '문서 형식: A', reviewed: null }] }
  expect(text(back, null)).toContain('자리 비운 동안 정한 것 1건')
})

test('명령: 자리 비움·복귀·갱신 간격', async () => {
  expect(C.parseCommand('away previews')).toEqual({ kind: 'away', previews: true })
  expect(C.parseCommand('back')).toEqual({ kind: 'back' })
  expect(C.parseCommand('rate 5')).toEqual({ kind: 'rate', seconds: 5 })
  expect(C.parseCommand('rate 3').kind).toBe('error')
  expect([C.stepRate(1, -1), C.stepRate(1, 1), C.stepRate(5, 1), C.stepRate(10, 1), C.stepRate(3, -1)]).toEqual([1, 2, 10, 10, 2])
  expect(C.schedArgs({ kind: 'away', previews: true }, SNAP)).toEqual({ args: ['away', '--previews', '--reason=TUI에서 자리 비움'] })
  const snap = { ...SNAP, away_log: [{ at: '', kind: 'preview' as const, key: 'UI', text: '', reviewed: null }] }
  expect(C.schedArgs({ kind: 'awayReview', key: 'UI', redo: false }, snap)).toEqual({ args: ['away-review', 'UI', '--keep'] })
  expect('error' in C.schedArgs({ kind: 'awayReview', key: 'UI', redo: true }, snap)).toBe(true)
  expect('error' in C.schedArgs({ kind: 'awayReview', key: 'NOPE', redo: false }, snap)).toBe(true)
})

test('명령 탭: 자리 비움 버튼과 갱신 간격 − +', async ($, on) => {
  const calls = engine(on)
  await start($)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'tab-commands' })
  expect(await ui.find({ type: 'Text', text: /갱신 1초/ })).toBeDefined() // 기본 1초
  await ui.press({ key: 'rate-up' })
  expect(await ui.find({ type: 'Text', text: /갱신 2초/ })).toBeDefined()
  await ui.select({ key: 'decision-mode', value: 'away' })
  await ui.press({ key: 'decision-mode-apply' })
  expect(calls.argv.some(a => a.includes('decision-policy') && a.includes('away'))).toBe(true)
  await ui.unmount()
})

test('승인·결정 탭: 자리 비운 동안 정한 것을 이대로 두거나 다시 정한다', async ($, on) => {
  const calls = engine(on, { ...SNAP, away_log: [{ at: '', kind: 'decision', key: 'D-0', text: '레이아웃: A (카드)', reviewed: null }] })
  await start($)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'tab-decisions' })
  expect(await ui.find({ type: 'Text', text: /자리 비운 동안 자동으로 정한 것 1건/ })).toBeDefined()
  await ui.press({ key: 'redo-D-0' })
  expect(calls.argv.find(a => a.includes('away-review'))?.slice(-3)).toEqual(['away-review', 'D-0', '--redo'])
  await ui.unmount()
})

test('미리보기 수정 요청은 한 번 더 확인한 뒤에만 보낸다(승인 대신 잘못 쓰는 일)', async ($, on) => {
  const calls = engine(on)
  await start($)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'tab-decisions' })
  await ui.input({ key: 'fb-UI', text: '좋은것 같아!!!' })
  expect(calls.argv.some(a => a.includes('revise'))).toBe(false)
  expect(await ui.find({ type: 'Text', text: /수정 요청으로 보낼까/ })).toBeDefined()
  await ui.press({ key: 'fb-cancel-UI' })
  expect(calls.argv.some(a => a.includes('revise'))).toBe(false)
  await ui.input({ key: 'fb-UI', text: '버튼을 더 크게' })
  await ui.press({ key: 'fb-send-UI' })
  expect(calls.argv.find(a => a.includes('revise'))?.slice(-4)).toEqual(['--version', '2', '--feedback=버튼을 더 크게', '--by', 'tui'].slice(-4))
  await ui.unmount()
})

test('에이전트 트리: 실행 중 작업 → 대기 작업 → 끝남(접힘), 미연결은 따로', async () => {
  const snap = { ...SNAP, tasks: [task({ id: 'A', state: 'IMPLEMENTING' }), task({ id: 'B', state: 'AWAITING_APPROVAL' }), task({ id: 'Z', state: 'COMPLETED' })] }
  const runs = [
    run({ agentId: 'z', role: 'implementer', task: 'Z', pool: 'implement', attempt: 1, status: 'completed', startedMs: 0, endedMs: 5 }),
    run({ agentId: 'b', role: 'designer', task: 'B', pool: 'preview', attempt: 1, status: 'idle', startedMs: 1, endedMs: 6 }),
    run({ agentId: 'a', role: 'implementer', task: 'A', pool: 'implement', attempt: 1, startedMs: 2 }),
    run({ agentId: 'x', role: 'Explore', description: '탐색', startedMs: 3 }),
  ]
  const t = (showDone: boolean) => C.agentLines(snap, runs, 60_000, null, showDone, 100).map(l => l.map(s => s.text).join(''))
  const folded = t(false)
  expect(folded[0]).toBe('실행 중 2 · 대기 1 · 끝남 1')
  expect(folded.findIndex(x => x.startsWith('● A'))).toBeLessThan(folded.findIndex(x => x.startsWith('? B')))
  expect(folded.some(x => x.includes('끝남 1 (접힘)'))).toBe(true)
  expect(folded.some(x => x.startsWith('✓ Z'))).toBe(false)
  expect(folded.findIndex(x => x.includes('미연결'))).toBeGreaterThan(folded.findIndex(x => x.includes('끝남 1')))
  expect(t(true).some(x => x.startsWith('✓ Z'))).toBe(true)
})

test('에이전트 트리: 열을 한글 폭으로 맞추고, 좁으면 오른쪽 열부터 뺀다', async () => {
  const snap = { ...SNAP, tasks: [task({ id: 'A', state: 'IMPLEMENTING' })] }
  const runs = [
    run({ agentId: 'd', role: 'designer', task: 'A', pool: 'preview', attempt: 1, status: 'idle', startedMs: 0, endedMs: 660_000, tools: 27, tokens: 21_000, lastTool: 'Write' }),
    run({ agentId: 'i', role: 'implementer', task: 'A', pool: 'implement', attempt: 2, startedMs: 0, tools: 34, tokens: 53_000, lastTool: 'Bash' }),
  ]
  const rows = (w: number) => C.agentLines(snap, runs, 600_000, null, false, w).slice(2, 4)
  const wide = rows(100)
  const at = (l: C.Line, i: number) => C.lineWidth(l.slice(0, i))
  for (const i of [3, 4, 5, 6]) expect(at(wide[0] ?? [], i)).toBe(at(wide[1] ?? [], i)) // 역할·단계·시간·도구 열 시작이 같다
  expect(wide.map(l => l.map(s => s.text).join('')).join('\n')).toContain('Bash')
  const narrow = rows(48).map(l => l.map(s => s.text).join(''))
  expect(narrow.join('\n')).not.toContain('Bash')
  expect(narrow.join('\n')).not.toContain('53k')
  expect(narrow.join('\n')).not.toContain('도구 34')
  expect(narrow[0]).toContain('implementer') // 실행 중이 대기보다 위
})

test('처음 열면(시작 준비 전) 빈 스냅샷을 만들어 명령 탭 버튼을 바로 쓸 수 있다', async ($, on) => {
  const calls = engine(on, { ...SNAP, tasks: [], decisions: [], total: 0, completed: 0, counts: {} }, { noSnapshotYet: true })
  await start($)
  expect(calls.argv.some(a => a.includes('snapshot'))).toBe(true)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'tab-commands' })
  await ui.select({ key: 'decision-mode', value: 'away' })
  await ui.press({ key: 'decision-mode-apply' })
  expect(calls.argv.some(a => a.includes('decision-policy') && a.includes('away'))).toBe(true)
  await ui.unmount()
})

test('에이전트 트리: 작업 줄은 ID를 자르지 않고 제목을 붙인다', async () => {
  const snap = { ...SNAP, tasks: [task({ id: 'RECURRING-TX', title: '정기 거래 등록·자동 입력', state: 'DESIGNING' })] }
  const lines = C.agentLines(snap, [run({ task: 'RECURRING-TX', pool: 'design', attempt: 1, role: 'architect' })], 0, null, false, 72)
    .map(l => C.fitLine(l, 72).map(s => s.text).join(''))
  expect(lines[1]).toContain('RECURRING-TX  설계')
  expect(lines[1]).toContain('정기 거래 등록')
  const narrow = C.agentLines(snap, [run({ task: 'RECURRING-TX', pool: 'design', attempt: 1, role: 'architect' })], 0, null, false, 30)
    .map(l => C.fitLine(l, 30).map(s => s.text).join(''))
  expect(narrow[1]).toContain('RECURRING-TX') // 좁아도 ID는 남고 제목이 줄어든다
})

test('자리 비운 동안 정한 것: 고른 안을 자르지 않고, 자세히는 다른 안·추천 이유를 보인다', async () => {
  const long = '별도 recurring_rules 테이블을 추가하고 해당 월을 조회할 때 자동으로 거래를 생성한다(확장성 확보, 기존 데이터는 그대로)'
  const snap = { ...SNAP, decisions: [{ ...SNAP.decisions[0]!, id: 'D-9', title: '정기 거래 저장 방식', kind: 'architecture', state: 'ANSWERED' as const, choice: 'A', by: 'away',
    options: [{ key: 'A', label: long, pros: '', cons: '' }, { key: 'B', label: '기존 transactions에 recurring 열 추가', pros: '', cons: '기존 스키마 변경' }], why: '기존 데이터를 건드리지 않는다' }] }
  const e = { at: '', kind: 'decision' as const, key: 'D-9', text: '…', reviewed: null, choice: 'A' }
  const closed = C.awayLines(snap, e, 60, false).map(l => l.map(s => s.text).join(''))
  expect(closed.length).toBeGreaterThan(2) // 줄바꿈해서 보인다
  expect(closed[closed.length - 1]).toContain('데이터는 그대로)') // 끝까지 잘리지 않는다
  expect(closed.join('')).not.toContain('다른 안')
  const open = C.awayLines(snap, e, 120, true).map(l => l.map(s => s.text).join('')).join('\n')
  expect(open).toContain('다른 안 B: 기존 transactions에 recurring 열 추가 (단점: 기존 스키마 변경)')
  expect(open).toContain('추천 이유: 기존 데이터를 건드리지 않는다')
  expect(open).toContain('종류 architecture')
})

test('에이전트 트리: 설계 작업이 아니면 "이 작업 뒤에 이어짐"으로 이유를 보인다', async () => {
  const snap = { ...SNAP, tasks: [task({ id: 'FIX', state: 'COMPLETED' }), task({ id: 'UI', state: 'PENDING', deps: ['FIX'] })] }
  const text = C.agentLines(snap, [run({ task: 'FIX', pool: 'verify', attempt: 1, role: 'verifier', status: 'idle' })], 0, null, false, 120)
    .map(l => l.map(s => s.text).join(''))
  expect(text[1]).toContain('UI가 이 작업 뒤에 이어짐')
  expect(text[1]).not.toContain('계약')
})

test('유령 슬롯 경고는 슬롯을 잡은 지 1분이 지나서도 실행이 안 보일 때만', async () => {
  const at = Date.parse('2026-11-24T10:00:00+09:00')
  const snap = { ...SNAP, tasks: [task({ id: 'A', state: 'IMPLEMENTING', updated: '2026-11-24T10:00:00+09:00' })] }
  expect(C.anomalies(snap, [], at + 10_000, 600_000).map(a => a.kind)).toEqual([])
  expect(C.anomalies(snap, [], at + 61_000, 600_000).map(a => a.kind)).toEqual(['ghost'])
})

test('통합·지표: 통합은 커밋 범위만, 상태별 시간은 막대로', async () => {
  const snap = { ...SNAP, integration_log: [{ time: '', status: 'MERGED', task: 'LABEL-FIX', message: "'integration' 23735d2 -> 7905f0b, 임시 worktree에서 병합·전체 테스트 통과" }],
    metrics: { ...SNAP.metrics, seconds_by_state: { DESIGNING: 900, IMPLEMENTING: 720, AWAITING_APPROVAL: 36 } } }
  const g = C.gitLines(snap, 70).map(l => l.map(s => s.text).join(''))
  expect(g.some(t => t.includes('LABEL-FIX') && t.includes('23735d2 → 7905f0b') && !t.includes('임시'))).toBe(true)
  const m = C.metricLines(snap, [], { costUsd: 58.11, contextPct: 12 }, 70).map(l => l.map(s => s.text).join(''))
  const bars = m.filter(t => t.includes('█'))
  expect(bars.length).toBe(3)
  expect((bars[0]?.match(/█/g) ?? []).length).toBeGreaterThan((bars[1]?.match(/█/g) ?? []).length)
  expect(m.join('\n')).not.toContain('컨텍스트') // 머리말에 있다
  expect(m.join('\n')).toContain('$58.11')
})

test('활동: 긴 줄은 다음 줄로 넘긴다', async () => {
  const long = 'BUDGET-OVERFLOW 경고: BUDGET-OVERFLOW 구현 슬롯을 잡았지만 이 세션에서 실행이 보이지 않는다(다른 세션일 수 있다)'
  const out = C.activityLines(null, [{ atMs: 0, level: 'warn', source: 'mod', task: null, text: long }], false, 0, 50).map(l => l.map(s => s.text).join(''))
  expect(out.length).toBe(2)
  expect(out[1]?.startsWith(' '.repeat(12))).toBe(true)
})

test('e2e5: 작업 탭은 긴 ID를 자르지 않고, 우선순위는 배정 전 작업에만', async () => {
  const snap = { ...SNAP, tasks: [task({ id: 'BUDGET-OVERFLOW', title: '예산 초과 강조' }), ...SNAP.tasks] }
  const rows = C.queueLines(snap, 'all', null, 120).map(r => r.line.map(s => s.text).join(''))
  expect(rows.some(t => t.includes('BUDGET-OVERFLOW '))).toBe(true)
  expect(C.canReprioritize(task({ state: 'READY' }))).toBe(true)
  expect(C.canReprioritize(task({ state: 'BLOCKED' }))).toBe(true)
  expect(C.canReprioritize(task({ state: 'IMPLEMENTING' }))).toBe(false)
  expect(C.canReprioritize(task({ state: 'VERIFYING' }))).toBe(false)
})

test('e2e5: 아무것도 돌지 않고 2분 넘게 그대로면 머리말에 멈춘 이유를 보인다', async () => {
  const at = C.ms(SNAP.generated_at) ?? 0
  const idle = { ...SNAP, slots: { ...SNAP.slots, implement: { used: 0, limit: 3 } },
    tasks: [task({ id: 'R', state: 'RETRY', runnable: false, pool: null }), task({ id: 'UI', state: 'AWAITING_APPROVAL', runnable: false, pool: null })] }
  expect(C.stuckReason(idle, [], at + 60_000)).toBeNull() // 2분 전에는 말하지 않는다
  const why = C.stuckReason(idle, [], at + 3 * 3600_000) ?? ''
  expect(why).toContain('결정 D-1')
  expect(why).toContain('승인 UI')
  expect(C.stuckReason(idle, [run({ status: 'running' })], at + 3 * 3600_000)).toBeNull() // 무언가 돌면 멈춘 게 아니다
  expect(C.stuckReason({ ...idle, tasks: [...idle.tasks, task({ id: 'GO', runnable: true })] }, [], at + 3 * 3600_000)).toBeNull()
  const head = C.headerLines(idle, { ...C.EMPTY_META, checkedMs: at }, [], at + 3 * 3600_000, [], 120, 1000).map(l => l.map(s => s.text).join(''))
  expect(head.some(t => t.startsWith('■ 멈춤 3h'))).toBe(true)
})

test('설계 검토: marker의 review 풀을 잇고, review 명령은 off|deep|ask만', async () => {
  expect(C.parseCommand('review ask')).toEqual({ kind: 'reviewMode', mode: 'ask' })
  expect(C.parseCommand('review maybe').kind).toBe('error')
  expect(C.schedArgs({ kind: 'reviewMode', mode: 'off' }, SNAP)).toEqual({ args: ['review-mode', 'off'] })
  expect(C.POOL_LABEL.review).toBe('설계 검토')
})

test('승인·결정 탭: 자동으로 정한 것은 최근 3건만 보이고, 모두 보기·모두 이대로 둠', async ($, on) => {
  const log = ['D-a', 'D-b', 'D-c', 'D-d', 'D-e'].map(k => ({ at: '', kind: 'decision' as const, key: k, text: `${k}: A`, reviewed: null }))
  const calls = engine(on, { ...SNAP, away_log: log })
  await start($)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'tab-decisions' })
  expect(await ui.find({ type: 'Button', key: 'keep-D-e' })).toBeDefined()
  expect(await ui.find({ type: 'Button', key: 'keep-D-a' })).toBeUndefined() // 접혀 있다
  await ui.press({ key: 'away-all' })
  expect(await ui.find({ type: 'Button', key: 'keep-D-a' })).toBeDefined()
  await ui.press({ key: 'keep-all' })
  expect(calls.argv.filter(a => a.includes('away-review')).length).toBe(5)
  await ui.unmount()
})

test('현황 탭도 긴 작업 ID를 자르지 않고 제목을 줄인다', async () => {
  const snap = { ...SNAP, tasks: [task({ id: 'RECURRING-FRONTEND', title: '정기 거래 관리 및 표시 UI 구현', state: 'AWAITING_APPROVAL', runnable: false, pool: null })] }
  const text = C.overviewLines(snap, [], 0, 90).map(l => l.map(s => s.text).join(''))
  expect(text.some(t => t.includes('RECURRING-FRONTEND '))).toBe(true)
  expect(text.every(t => C.strWidth(t) <= 90)).toBe(true)
})

test('4-15 밤하늘 색: 명령 탭에서 고르면 Pane 바탕과 글자색이 바뀌고 다시 열어도 남는다', async ($, on) => {
  engine(on)
  await start($)
  expect(C.parseCommand('palette night')).toEqual({ kind: 'palette', palette: 'night' })
  expect(C.parseCommand('palette pink').kind).toBe('error')
  expect(C.toneColor('theme', 'ok')).toBe('success')
  expect(C.toneColor('night', 'ok')).toMatch(/^#/)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'tab-commands' })
  await ui.select({ key: 'palette', value: 'night' })
  expect((await ui.drawn() as { props?: Record<string, unknown> }).props?.backgroundColor).toBe(C.paletteBackground('night'))
  expect((await ui.findAll({ type: 'Text', text: /^✦ ASTERBRAID/ })).some(x => x.props.color === C.toneColor('night', 'accent'))).toBe(true) // 줄 안의 제목 조각
  await ui.select({ key: 'palette', value: 'theme' })
  expect((await ui.drawn() as { props?: Record<string, unknown> }).props?.backgroundColor).toBeUndefined()
  await ui.unmount()
})

test('6-1 화면 통일: 탭 안 소제목은 모두 같은 구분선, 표 열은 긴 ID를 자르지 않는다', async () => {
  const long = 'RECURRING-FRONTEND-LONG-ID'
  const snap = {
    ...SNAP,
    tasks: [task({ id: long, state: 'READY_TO_MERGE', branch: 'auto/x', head: 'abc', timeline: [{ state: 'READY', start: '2026-11-24T10:00:00+09:00', end: null }] })],
    integration_log: [{ time: '2026-11-24T10:01:00+09:00', status: 'MERGED', task: long, message: 'aaaaaaa -> bbbbbbb' }],
  }
  const has = (ls: C.Line[]) => ls.map(l => l.map(s => s.text).join('')).some(t => t.includes(`${long} `))
  expect(has(C.gitLines(snap, 90))).toBe(true)
  expect(has(C.timelineLines(snap, C.ms('2026-11-24T10:05:00+09:00') ?? 0, 90))).toBe(true)
  const runs = [run({ agentId: 'z', task: null, status: 'completed' }), run({ agentId: 'y', task: long, status: 'completed' })]
  const heads = C.agentLines(snap, runs, 0, null, true, 90).filter(l => l[0]?.text.startsWith('── '))
  expect(heads.length).toBeGreaterThanOrEqual(2) // 끝남·미연결도 구분선으로
})

test('6-1 설계 문서·설계 검토 결과: 작업을 펼치면 보이고, 문서는 Pane에서 열어 본다(docs/asterbraid 안만)', async ($, on) => {
  const t = task({
    id: 'DSN', state: 'READY', notes: ['docs/asterbraid/design/2026-11-26-1000-DSN.md'],
    design_review: { verdict: 'FAIL', by: 'verifier-dsn', at: '2026-11-26T10:00:00+09:00', notes: '오류 계약이 빠졌다' },
  })
  const bad = task({ id: 'BAD', state: 'READY', notes: ['../secret.md'] })
  engine(on, { ...SNAP, tasks: [t, bad] })
  await start($)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.press({ key: 'tab-tasks' })
  await ui.press({ key: 'q-DSN' })
  expect(await ui.find({ type: 'Text', text: /설계 검토 FAIL · verifier-dsn · 오류 계약이 빠졌다/ })).toBeDefined()
  await ui.press({ key: 'doc-DSN' })
  expect(await ui.find({ type: 'Text', text: /^계약 본문$/ })).toBeDefined() // 제목과 본문이 별도 줄
  expect(await ui.find({ type: 'Text', text: /계약 본문/ })).toBeDefined()
  await ui.press({ key: 'doc-close' })
  expect(await ui.find({ type: 'Text', text: /계약 본문/ })).toBeUndefined()
  await ui.press({ key: 'q-BAD' })
  await ui.press({ key: 'doc-BAD' })
  expect(await ui.find({ type: 'Text', text: /docs\/asterbraid 밖/ })).toBeDefined()
  await ui.unmount()
})

test('e2e6 E6-5: 머리말·에이전트 탭의 "실행 미확인"도 1분 유예를 지킨다(경고와 같은 기준)', async () => {
  const at = Date.parse('2026-11-24T10:00:00+09:00')
  const snap = { ...SNAP, tasks: [task({ id: 'A', state: 'IMPLEMENTING', updated: '2026-11-24T10:00:00+09:00' })] }
  const text = (ls: C.Line[]) => ls.map(l => l.map(s => s.text).join('')).join('\n')
  expect(text(C.headerLines(snap, C.EMPTY_META, [], at + 16_000, [], 90, 1000))).not.toContain('실행 미확인')
  expect(text(C.agentLines(snap, [], at + 16_000, null))).not.toContain('관측되지 않은')
  expect(text(C.headerLines(snap, C.EMPTY_META, [], at + 61_000, [], 90, 1000))).toContain('실행 미확인 1')
})

test('e2e7: turn.complete는 목록에 없는 검토자를 대기로 옮기고 멈춤 경고를 끝낸다', async () => {
  const runs = [run({ agentId: 'reviewer', task: 'T', pool: 'review' }), run({ agentId: 'isolated' })]
  const next = C.applyTurnComplete(runs, 'reviewer', 100)
  expect(next[0]?.status).toBe('idle')
  expect(next[0]?.endedMs).toBe(100)
  expect(next[1]?.status).toBe('running')
  expect(C.applyAgentList(runs, [], 100)[0]?.status).toBe('running')
  expect(C.anomalies(null, next, 1000, 200).some(a => a.agentId === 'reviewer')).toBe(false)
  expect(C.applyTurnComplete([run({ status: 'failed' })], 'a1', 100)[0]?.status).toBe('failed')
})

test('e2e7: 실행 중 설계 검토는 대기 대신 검토로 보이고 보류는 시작 가능한 작업 아래다', async () => {
  const snap = { ...SNAP, tasks: [task({ id: 'BLOCK', state: 'BLOCKED', runnable: false }), task({ id: 'WAIT', state: 'PENDING' }), task({ id: 'REVIEW', state: 'PENDING', review_pending: true })] }
  const runs = [run({ task: 'REVIEW', pool: 'review' })]
  const rows = C.queueLines(snap, 'all', null, 120, false, {}, runs)
  expect(rows.filter(r => r.id).map(r => r.id)).toEqual(['REVIEW', 'WAIT', 'BLOCK'])
  expect(rows.find(r => r.id === 'REVIEW')?.line.map(s => s.text).join('')).toContain('설계 검토')
  expect(C.queueLines(snap, 'active', null, 120, false, {}, runs).filter(r => r.id).map(r => r.id)).toEqual(['REVIEW'])
  expect(C.overviewLines(snap, runs, 0, 120).map(l => l.map(s => s.text).join('')).join('\n')).toContain('설계 검토')
})

test('e2e7: 펼친 상세·작업 제목·브랜치·worktree는 좁아도 내용이 사라지지 않는다', async () => {
  const criteria = '완료 기준을 빠짐없이 확인한다 '.repeat(12).trim()
  const branch = 'task/very-long-branch-name-that-must-not-disappear'
  const worktree = '/repo/.worktrees/very-long-directory-that-must-not-disappear'
  const snap = { ...SNAP, tasks: [task({ id: 'T', criteria, branch, worktree, title: '아주 긴 작업 제목을 모두 확인한다' })] }
  const rows = C.queueLines(snap, 'all', 'T', 32)
  const text = rows.map(r => r.line.map(s => s.text).join('')).join('\n').replace(/\s/g, '')
  expect(text).toContain(criteria.replace(/\s/g, ''))
  expect(text).toContain(branch)
  expect(text).toContain(worktree)
  const agents = C.agentLines(snap, [run({ task: 'T', cwd: worktree })], 0, 'a1', false, 32)
  const joined = agents.map(l => C.fitLine(l, 32).map(s => s.text).join('')).join('\n').replace(/\s/g, '')
  expect(joined).toContain('아주긴작업제목을모두확인한다')
  expect(joined).toContain(branch)
  expect(joined).toContain(worktree)
  expect(agents.every(l => C.lineWidth(l) <= 32)).toBe(true)
})

test('머리말은 사용자 차례를 자리 비움 안내보다 먼저 보여 준다', async () => {
  const text = (snap: Snapshot) => C.headerLines(snap, C.EMPTY_META, [], 0, [], 120, 1000)
    .map(l => l.map(s => s.text).join('')).join('\n')
  const away = text({ ...SNAP, away: { at: '', previews: true, reason: '' } })
  expect(away.indexOf('▶ 사용자 차례')).toBeLessThan(away.indexOf('☾ 자리 비움 중'))
  const back = text({ ...SNAP, away: null, away_log: [{ at: '', kind: 'decision', key: 'D-1', text: 'A', reviewed: null }] })
  expect(back.indexOf('▶ 사용자 차례')).toBeLessThan(back.indexOf('☾ 자리 비운 동안'))
})
