// The status mod's behaviour, run with `claude plugin test` on a rendered copy by tools/test_claude_mod.py
// (the copy `usage.configure_claude` installs carries no tests). REQ-000000414.
import { expect, test } from 'claude-code/testing'

import type { Status } from '../types'
import { ICONS, compact, lines, report } from './register'

const quiet: Status = {
  project: 'little-meals',
  server: { known: true, running: false, listening: false, unit: 'inactive' },
  running: { count: 0, items: [] },
  tasks_attention: { count: 0, items: [] },
  failed_jobs: { count: 0, items: [], hours: 2 },
  scheduled: { count: 0, items: [], hours: 1 },
  policies: { count: 0, items: [] },
  incidents: { count: 0, items: [], hours: 2 },
  dead_pillars: { count: 0, items: [], of: 10 },
  unavailable: [],
}

const busy: Status = {
  ...quiet,
  server: { known: true, running: true, listening: true, unit: 'active' },
  running: { count: 2, items: [{ id: 'j1', title: 'fix login', href: '/jobs/j1' }, { id: 'j2', title: 'build' }] },
  tasks_attention: { count: 3, items: [{ id: 'r1', title: 'refactor' }] },
  failed_jobs: { count: 1, items: [{ id: 'j3', title: 'deploy' }], hours: 2 },
  scheduled: { count: 2, items: [{ id: 's1', title: 'nightly' }], hours: 1 },
  policies: { count: 4, items: [{ id: 'p1', title: 'commit-bumps-version' }] },
  incidents: { count: 1, items: [{ id: 'i1', title: 'ValueError in agent' }], hours: 2 },
  dead_pillars: { count: 2, items: [{ id: 'policy', title: 'policy' }], of: 10 },
}

test('the phone shows only what is not zero, and counts only where asked', () => {
  expect(compact(quiet)).toBe(ICONS.serverDown)
  expect(compact(busy)).toBe(
    [ICONS.serverUp, `${ICONS.running}2`, ICONS.tasks, `${ICONS.failed}1`, ICONS.scheduled, ICONS.policies,
      `${ICONS.incidents}1`, `${ICONS.dead}2`].join('  '),
  )
})

test('a project without a server shows no server icon, and nothing at all is a check mark', () => {
  expect(compact({ ...quiet, server: null })).toBe('✓')
  expect(compact({ incidents: { count: 0, items: [] }, dead_pillars: { count: 0, items: [], of: 10 }, unavailable: [] })).toBe('✓')
})

test('a pillar that did not answer is unknown, never zero; little-workshop down says so', () => {
  expect(compact({ ...quiet, server: null, running: { count: null, items: [] }, unavailable: ['orchestration'] })).toBe(ICONS.unknown)
  expect(compact({ error: 'little-workshop is not answering' })).toBe(`${ICONS.offline} little-workshop is not answering`)
})

test('the detailed view lists every topic, zeros included', () => {
  const shown = lines(quiet).map(line => `${line.label}: ${line.value}`)
  expect(shown).toEqual([
    'Server: stopped',
    'Running jobs: 0',
    'Tasks needing attention: 0',
    'Failed jobs, last 2 h: 0',
    'Scheduled, next 1 h: 0',
    'Failing policies: 0',
    'Incidents, last 2 h: 0',
    'Dead pillars: 0 of 10',
  ])
  expect(lines({ ...quiet, server: null })[0]?.value).toBe('none')
})

test('outside a project the detailed view shows the workshop alone', () => {
  const shown = lines({ project: null, incidents: { count: 1, items: [], hours: 2 }, dead_pillars: { count: 0, items: [], of: 10 } })
  expect(shown.map(line => line.key)).toEqual(['incidents', 'dead'])
})

const PANE = {
  plugin: 'little-workshop-status', component: 'Pane', requestId: 'workshop',
  props: { title: 'little-workshop', isFocused: false, bodyColumns: 60, placement: 'inline',
    scroll: { top: 0, bodyRows: 10, contentRows: 10 }, view: {} },
} as const

test('before the first read the card says it is reading', async $ => {
  for (const surface of ['mobile', 'desktop', 'terminal'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    expect(await ui.find({ type: 'Text' })).toBeDefined()
    await ui.unmount()
  }
})

test('the card draws the icons on the phone and every topic with links elsewhere', async ($, on) => {
  const fetched: string[] = []
  on('session.cwd', () => ({ value: '/x/little-meals' }))
  on('session.surfaces', () => ({ value: ['mobile'] as const }))
  on('process.run', (_, e) => {
    const args = e.argv.join(' ')
    const out = args.includes('--show-toplevel') ? '/x/little-meals' : args.includes('--git-common-dir') ? '/x/little-meals/.git' : 'dev'
    return { value: { exitCode: 0, stdout: `${out}\n`, stderr: '' } }
  })
  on('fs.read', () => ({ value: '0.10.6\n' }))
  on('http.fetch', (_, e) => {
    fetched.push(e.url)
    return { value: { status: 200, ok: true, headers: {}, text: JSON.stringify(busy) } }
  })
  const opened: string[] = []
  on('ui.open', (_, e) => {
    opened.push(e.id)
    return { value: { isPlaced: true } }
  })
  const answer = await $.command.run({ command: 'ws', args: '' })
  expect(answer.text?.split('\n').slice(0, 3)).toEqual(['📦 Project: little-meals', '🌿 Branch: dev', '🏷️ Version: 0.10.6'])
  expect(answer.text).toContain('── little-workshop ──')
  expect(fetched).toEqual(['http://dashboard/status.json?project=little-meals'])
  expect(opened).toEqual(['workshop'])
  const phone = await $.ui.mount({ ...PANE, surface: 'mobile' })
  expect((await phone.find({ type: 'Text' }))?.text).toBe(compact(busy))
  await phone.unmount()
  for (const surface of ['desktop', 'terminal'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    expect(await ui.find({ type: 'Text', text: /Dead pillars: 2 of 10/ })).toBeDefined()
    expect((await ui.findAll({ type: 'Link' })).length).toBeGreaterThan(0)
    await ui.unmount()
  }
})

const NOW = Date.parse('2026-10-06T12:00:00Z')
const here = { project: 'little-meals', version: '0.10.6', branch: 'dev' }

test('the report names the checkout, the server and the tests, and counts and lists each topic', () => {
  const tests = { known: true, status: 'succeeded', ran: 120, passed: 120, failed: 0, at: '2026-10-06T10:00:00Z' }
  expect(report({ ...quiet, tests }, here, NOW).split('\n')).toEqual([
    '📦 Project: little-meals',
    '🌿 Branch: dev',
    '🏷️ Version: 0.10.6',
    `${ICONS.serverDown} Server: stopped`,
    '✅ Tests: 120 passed (2 h ago)',
    `${ICONS.running} Running jobs: 0`,
    '🛡️ Policies: all passing',
    '── little-workshop ──',
    '🟢 Pillars: 10 of 10 up',
    '🧯 Incidents (last 2 h): 0',
  ])
  const text = report({ ...busy, tests: { ...tests, passed: 117, failed: 3 } }, here, NOW)
  expect(text).toContain('🔴 Tests: 3 failing of 120 (2 h ago)')
  expect(text).toContain(`${ICONS.running} Running jobs: 2\n   • fix login\n   • build`)
  expect(text).toContain(`${ICONS.tasks} Needs you: 3\n   • refactor\n   • and 2 more`)
  expect(text).toContain(`${ICONS.failed} Failed jobs (last 2 h): 1\n   • deploy`)
  expect(text).toContain(`${ICONS.scheduled} Scheduled (next 1 h): 2\n   • nightly\n   • and 1 more`)
  expect(text).toContain(`${ICONS.policies} Failing policies: 4\n   • commit-bumps-version\n   • and 3 more`)
  expect(text).toContain(`${ICONS.dead} Pillars down (of 10): 2\n   • policy\n   • and 1 more`)
  expect(text).toContain(`${ICONS.incidents} Incidents (last 2 h): 1\n   • ValueError in agent`)
})

test('the report says what it cannot know and when little-workshop is down', () => {
  expect(report({ ...quiet, server: null, tests: null }, null, NOW)).toContain('▫️ Server: none')
  expect(report({ ...quiet, tests: { known: false } }, here, NOW)).toContain(`${ICONS.unknown} Tests: unknown`)
  expect(report({ ...quiet, running: { count: null, items: [] } }, here, NOW)).toContain(`${ICONS.unknown} Running jobs: unknown`)
  expect(report({ error: 'little-workshop is not answering' }, here, NOW)).toBe(`${ICONS.offline} little-workshop is not answering`)
})
