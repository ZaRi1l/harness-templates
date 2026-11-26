import type { On } from 'claude-code'
import { expect, mock, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import * as C from '../hooks/core'
import * as R from '../hooks/register'
import type { Snapshot } from '../types'

const SNAP: Snapshot = {
  schema: 'autonomous-dashboard', schema_version: 1, generated_at: '', generated_ms: 0,
  project: { root: '/repo', name: 'repo', state_dir: '/repo/.git/claude-auto' },
  integration: { branch: 'integration', head: null }, paused: null,
  total: 0, completed: 0, counts: {}, slots: {}, tasks: [], decisions: [], notes: [], events: [],
  integration_log: [], metrics: {}, anomalies: [], dashboard_html: '',
}
const setup = (on: On, snap = SNAP, failMode = false) => {
  mock.clock(on)
  const argv: string[][] = []
  const store = new Map<string, unknown>()
  on('command.register', () => ({ value: { command: 'asterbraid' } }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('session.machine', () => ({ value: { sysname: 'Linux', release: '6', machine: 'x86_64', packageManagers: [], runtimes: [] } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.status', () => ({ value: undefined }))
  on('store.get', (_, e) => ({ value: store.get(e.key) }))
  on('store.set', (_, e) => { store.set(e.key, e.value); return { value: undefined } })
  on('fs.stat', (_, e) => {
    if (e.path.endsWith('dashboard.error.json')) throw new Error('ENOENT')
    return { value: { kind: e.path.endsWith('.py') || e.path.endsWith('.json') ? 'file' : 'dir', size: 1, mtimeMs: 1, isLink: false, ...(e.resolve ? { realPath: e.path } : {}) } }
  })
  on('fs.read', () => ({ value: JSON.stringify(snap) }))
  on('process.run', (_, e) => {
    argv.push([...e.argv])
    if (failMode && e.argv.includes('decision-policy')) return { value: { exitCode: 1, stdout: '', stderr: 'refused', isStdoutTruncated: false, isStderrTruncated: false } }
    return { value: { exitCode: 0, stdout: e.argv[0] === 'git' ? '/repo/.git\n' : '{}', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  return argv
}
const start = async ($: Engine) => {
  await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })
  await $.command.run({ command: 'asterbraid', args: 'commands', origin: { kind: 'composer' }, presentation: { isFullscreen: true, columns: 160 } })
}
const PANE = {
  plugin: 'asterbraid-dashboard', component: 'Pane', requestId: 'asterbraid',
  props: { title: 'AsterBraid', isFocused: false, bodyColumns: 72, placement: 'dock', scroll: { offset: 0, bodyRows: 30 }, view: {} },
} as const

test('색 선택기는 모든 표면에서 여섯 팔레트를 고르고 작업 상태를 바꾸지 않는다', async ($, on) => {
  const argv = setup(on)
  await start($)
  for (const surface of ['terminal', 'desktop', 'vscode', 'mobile'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    expect(await ui.find({ key: 'palette', type: 'Select' })).toBeDefined()
    expect(R.PALETTES).toEqual(['theme', 'night', 'dracula', 'nord', 'catppuccin-mocha', 'slate-dark'])
    const before = argv.length
    for (const palette of R.PALETTES) {
      await ui.select({ key: 'palette', value: palette })
      expect((await ui.find({ key: 'palette' }))?.props.value).toBe(palette)
      expect((await ui.find({ type: 'Box' }))?.props.backgroundColor).toBe(R.paletteBackground(palette))
    }
    expect(argv.length).toBe(before)
    await ui.unmount()
  }
})

test('결정 모드는 고르기만 해서는 실행되지 않고 적용만 명령을 보낸다', async ($, on) => {
  const argv = setup(on)
  await start($)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  const before = argv.length
  await ui.select({ key: 'decision-mode', value: 'all' })
  expect(argv.length).toBe(before)
  expect(await ui.find({ type: 'Text', text: /현재: 직접 결정.*선택: 전부 위임/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /권한.*auto/ })).toBeDefined()
  await ui.press({ key: 'decision-mode-apply' })
  expect(argv.some(a => a.includes('decision-policy') && a.includes('all'))).toBe(true)
  expect(await ui.find({ key: 'cmd-pause' })).toBeDefined()
  expect(await ui.find({ key: 'cmd-away' })).toBeUndefined()
  await ui.unmount()
})

test('적용 실패는 현재 모드를 바꾸지 않고 선택한 초안을 남긴다', async ($, on) => {
  setup(on, SNAP, true)
  await start($)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  await ui.select({ key: 'decision-mode', value: 'all' })
  await ui.press({ key: 'decision-mode-apply' })
  expect(await ui.find({ type: 'Text', text: /현재: 직접 결정.*선택: 전부 위임/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: /refused/ })).toBeDefined()
  expect(await ui.find({ key: 'decision-mode-apply' })).toBeDefined()
  await ui.unmount()
})

test('읽기 전용에서도 색은 고르지만 결정 위임과 적용은 숨긴다', { options: { allowActions: false } }, async ($, on) => {
  setup(on)
  await start($)
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ key: 'palette' })).toBeDefined()
  expect(await ui.find({ key: 'decision-mode' })).toBeUndefined()
  expect(await ui.find({ key: 'decision-mode-apply' })).toBeUndefined()
  await ui.unmount()
})
