// The little-workshop status mod (REQ-000000414), written into ~/.claude/skills/little-workshop-status/ by
// little-workshop's Usage & Efficiency pillar (`usage.configure_claude`): do not edit the copy, the next apply
// replaces it. It reads the Dashboard's /status.json for the project the session works in and draws it:
// on the phone a line of icons above the prompt (only what is not zero), on the desktop and in the terminal
// every topic in detail, zeros included.
import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Checkout, Status, Topic } from '../types'

const DASHBOARD_SOCKET = '@@DASHBOARD_SOCKET@@'
const DASHBOARD_URL = '@@DASHBOARD_URL@@'
const PANE = 'workshop'
const TITLE = 'little-workshop'
const REFRESH_MS = 30_000
const DETAIL_ITEMS = 2 // the first items named on a line of the detailed view

const status = atom({ plugin: 'little-workshop-status', key: 'status' } as const, null)
const checkout = atom({ plugin: 'little-workshop-status', key: 'checkout' } as const, null)

export const ICONS = {
  serverUp: '🟢',
  serverDown: '⚪',
  running: '⚙️',
  tasks: '❗',
  failed: '❌',
  scheduled: '⏰',
  policies: '⛔',
  incidents: '🔥',
  dead: '💀',
  unknown: '❔',
  offline: '⚠️',
} as const

async function git($: EngineInterface, cwd: string, ...args: string[]): Promise<string | null> {
  try {
    const { exitCode, stdout } = await $.process.run(['git', ...args], { cwd, timeoutMs: 3000 })
    const out = stdout.trim()
    return exitCode === 0 && out !== '' ? out : null
  } catch {
    return null
  }
}

// The project, VERSION and branch of the checkout the session works in, as the session-title hook names them.
async function readCheckout($: EngineInterface): Promise<Checkout | null> {
  const cwd = await $.session.cwd()
  const top = await git($, cwd, 'rev-parse', '--show-toplevel')
  const common = await git($, cwd, 'rev-parse', '--path-format=absolute', '--git-common-dir')
  if (top === null || common === null) return null
  let version: string
  try {
    version = (await $.fs.read(`${top}/VERSION`)).trim() || '?'
  } catch {
    return null
  }
  let branch = (await git($, cwd, 'rev-parse', '--abbrev-ref', 'HEAD')) ?? '?'
  if (branch === 'HEAD') branch = (await git($, cwd, 'rev-parse', '--short', 'HEAD')) ?? 'detached'
  const parts = common.split('/')
  const last = parts.at(-1) ?? ''
  const project = last === '.git' ? (parts.at(-2) ?? '') : last.replace(/\.git$/, '')
  return { project, version, branch }
}

async function refresh($: EngineInterface): Promise<void> {
  const found = await readCheckout($)
  await update($, checkout, () => found)
  const query = found === null ? '' : `?project=${encodeURIComponent(found.project)}`
  let next: Status
  try {
    const answer = await $.http.fetch(`http://dashboard/status.json${query}`, { socketPath: DASHBOARD_SOCKET })
    next = answer.ok ? (JSON.parse(answer.text) as Status) : { error: `the Dashboard answered ${answer.status}` }
  } catch {
    next = { error: 'little-workshop is not answering' }
  }
  await update($, status, () => next)
}

// Work only while something draws the session: a headless run (an agent task) never reads the Dashboard.
async function refreshIfShown($: EngineInterface): Promise<void> {
  if ((await $.session.surfaces()).length > 0) await refresh($)
}

function counted(topic: Topic | undefined, icon: string): string | null {
  return topic !== undefined && topic.count !== null && topic.count > 0 ? `${icon}${topic.count}` : null
}

function flagged(topic: Topic | undefined, icon: string): string | null {
  return topic !== undefined && topic.count !== null && topic.count > 0 ? icon : null
}

// The phone's line: the project's icons, then the workshop's, each left out while its count is zero.
export function compact(s: Status | null): string {
  if (s === null) return '…'
  if (s.error !== undefined) return `${ICONS.offline} ${s.error}`
  const server = s.server == null ? null : !s.server.known ? ICONS.unknown : s.server.running ? ICONS.serverUp : ICONS.serverDown
  const parts = [
    server,
    counted(s.running, ICONS.running),
    flagged(s.tasks_attention, ICONS.tasks),
    counted(s.failed_jobs, ICONS.failed),
    flagged(s.scheduled, ICONS.scheduled),
    flagged(s.policies, ICONS.policies),
    counted(s.incidents, ICONS.incidents),
    counted(s.dead_pillars, ICONS.dead),
    (s.unavailable ?? []).length > 0 ? ICONS.unknown : null,
  ].filter((part): part is string => part !== null)
  return parts.length > 0 ? parts.join('  ') : '✓'
}

function ago(iso: string | null | undefined, now: number): string {
  if (!iso) return ''
  const at = Date.parse(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`)
  if (Number.isNaN(at)) return ''
  const minutes = Math.max(0, Math.round((now - at) / 60_000))
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  if (minutes < 48 * 60) return `${Math.round(minutes / 60)} h ago`
  return `${Math.round(minutes / 1440)} d ago`
}

const BULLET = '   • '

// A topic's items as list lines under its heading, the ones /status.json did not list counted at the end.
function listed(topic: Topic | undefined): string[] {
  const items = topic?.items ?? []
  const out = items.map(item => `${BULLET}${item.title ?? item.id}`)
  const rest = (topic?.count ?? 0) - items.length
  return rest > 0 ? [...out, `${BULLET}and ${rest} more`] : out
}

// What /ws answers: the project's state and the workshop's, a few lines read at a glance, each with its count.
export function report(s: Status | null, c: Checkout | null, now: number): string {
  if (s === null) return 'Reading little-workshop…'
  if (s.error !== undefined) return `${ICONS.offline} ${s.error}`
  const out: string[] = []
  const line = (icon: string, text: string) => out.push(`${icon} ${text}`)
  const known = (topic: Topic | undefined): topic is Topic & { count: number } => topic !== undefined && topic.count !== null
  // A heading with its count and its items listed under it; `always` shows it at zero too.
  const topic = (icon: string, label: string, t: Topic | undefined, always: boolean) => {
    if (!known(t)) return line(ICONS.unknown, `${label}: unknown`)
    if (t.count === 0 && !always) return
    line(icon, `${label}: ${t.count}`)
    out.push(...listed(t))
  }
  const project = c?.project ?? s.project
  if (project != null) line('📦', `Project: ${project}`)
  if (c !== null) {
    line('🌿', `Branch: ${c.branch}`)
    line('🏷️', `Version: ${c.version}`)
  }
  if (s.project != null) {
    if (s.server == null) line('▫️', 'Server: none')
    else if (!s.server.known) line(ICONS.unknown, 'Server: unknown')
    else line(s.server.running ? ICONS.serverUp : ICONS.serverDown, `Server: ${s.server.running ? 'running' : 'stopped'}`)
    const t = s.tests
    if (t == null) line('🧪', 'Tests: never run')
    else if (!t.known) line(ICONS.unknown, 'Tests: unknown')
    else if (t.status !== 'succeeded') line('🧪', `Tests: last run ${t.status} (${ago(t.at, now)})`)
    else if ((t.failed ?? 0) > 0) line('🔴', `Tests: ${t.failed} failing of ${t.ran} (${ago(t.at, now)})`)
    else line('✅', `Tests: ${t.passed} passed (${ago(t.at, now)})`)
    topic(ICONS.running, 'Running jobs', s.running, true)
    topic(ICONS.tasks, 'Needs you', s.tasks_attention, false)
    topic(ICONS.failed, `Failed jobs (last ${s.failed_jobs?.hours ?? 2} h)`, s.failed_jobs, false)
    topic(ICONS.scheduled, `Scheduled (next ${s.scheduled?.hours ?? 1} h)`, s.scheduled, false)
    if (known(s.policies) && s.policies.count === 0) line('🛡️', 'Policies: all passing')
    else topic(ICONS.policies, 'Failing policies', s.policies, true)
  }
  out.push('── little-workshop ──')
  const d = s.dead_pillars
  if (known(d) && d.count === 0) line('🟢', `Pillars: ${d.of} of ${d.of} up`)
  else topic(ICONS.dead, `Pillars down (of ${d?.of ?? '?'})`, d, true)
  topic(s.incidents?.count ? ICONS.incidents : '🧯', `Incidents (last ${s.incidents?.hours ?? 2} h)`, s.incidents, true)
  return out.join('\n')
}

type Line = { key: string; icon: string; label: string; value: string; topic?: Topic }

// The detailed view's lines: every topic, zeros included.
export function lines(s: Status): Line[] {
  const value = (topic: Topic | undefined, of?: number | null) =>
    topic === undefined || topic.count === null ? 'unknown' : of == null ? String(topic.count) : `${topic.count} of ${of}`
  const out: Line[] = []
  if (s.project != null) {
    const server = s.server == null ? 'none' : !s.server.known ? 'unknown' : s.server.running ? 'running' : 'stopped'
    out.push({ key: 'server', icon: s.server?.running ? ICONS.serverUp : ICONS.serverDown, label: 'Server', value: server })
    out.push({ key: 'running', icon: ICONS.running, label: 'Running jobs', value: value(s.running), topic: s.running })
    out.push({ key: 'tasks', icon: ICONS.tasks, label: 'Tasks needing attention', value: value(s.tasks_attention), topic: s.tasks_attention })
    out.push({ key: 'failed', icon: ICONS.failed, label: `Failed jobs, last ${s.failed_jobs?.hours ?? 2} h`, value: value(s.failed_jobs), topic: s.failed_jobs })
    out.push({ key: 'scheduled', icon: ICONS.scheduled, label: `Scheduled, next ${s.scheduled?.hours ?? 1} h`, value: value(s.scheduled), topic: s.scheduled })
    out.push({ key: 'policies', icon: ICONS.policies, label: 'Failing policies', value: value(s.policies), topic: s.policies })
  }
  out.push({ key: 'incidents', icon: ICONS.incidents, label: `Incidents, last ${s.incidents?.hours ?? 2} h`, value: value(s.incidents), topic: s.incidents })
  out.push({ key: 'dead', icon: ICONS.dead, label: 'Dead pillars', value: value(s.dead_pillars, s.dead_pillars?.of), topic: s.dead_pillars })
  return out
}

function heading(c: Checkout | null, s: Status | null): string {
  if (c !== null) return `${c.project} · ${c.version} · ${c.branch}`
  return s?.project ?? TITLE
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'ws', description: 'Show the little-workshop status of this project' })
    void refreshIfShown($)
    $.clock.every(REFRESH_MS, () => {
      void refreshIfShown($)
    })
    return next(e)
  })

  on('session.attach', async ($, e, next) => {
    void refresh($)
    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    void refreshIfShown($)
    return next(e)
  })

  on('command.run', { command: 'ws' }, async $ => {
    await refresh($)
    // The phone draws no plugin pane but shows a command's text: a short status report answers /ws everywhere,
    // and the terminal also gets the detailed pane.
    void $.ui.open({ id: PANE, title: TITLE }).catch(() => undefined)
    return { text: report(await read($, status), await read($, checkout), Date.now()) }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const s = await read($, status)
    const c = await read($, checkout)
    const { Box, Text, Link } = $.ui.resolve(e)
    if (e.surface === 'mobile') return <Text key="line">{compact(s)}</Text>
    return (
      <Box flexDirection="column">
        <Text key="heading" bold wrap="truncate-end">
          {heading(c, s)}
        </Text>
        {s === null ? (
          <Text key="loading" dimColor>
            Reading little-workshop…
          </Text>
        ) : s.error !== undefined ? (
          <Text key="error">{compact(s)}</Text>
        ) : (
          lines(s).map(line => (
            <Box key={line.key} flexDirection="row" gap={1}>
              <Text wrap="truncate-end">
                {line.icon} {line.label}: <Text bold>{line.value}</Text>
              </Text>
              {(line.topic?.items ?? []).slice(0, DETAIL_ITEMS).map(item => (
                <Link key={`${line.key}-${item.id}`} href={`${DASHBOARD_URL}${item.href ?? ''}`} label={item.title ?? item.id} />
              ))}
            </Box>
          ))
        )}
        {s?.project != null && (
          <Link key="open" href={`${DASHBOARD_URL}/status/${encodeURIComponent(s.project)}`} label="Open the status page" />
        )}
      </Box>
    )
  })

  // The Windows app draws the detailed view above the prompt, always there (once it loads plugins).
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.surface !== 'desktop' || e.props.hasSurvey) return next(e)
    const s = await read($, status)
    const c = await read($, checkout)
    if (s === null) return next(e)
    const { Box, Text } = $.ui.resolve(e)
    return (
      <Box flexDirection="column">
        <Text key="heading" bold wrap="truncate-end">
          {heading(c, s)}
        </Text>
        {s.error !== undefined ? (
          <Text key="error">{compact(s)}</Text>
        ) : (
          lines(s).map(line => (
            <Text key={line.key} wrap="truncate-end">
              {line.icon} {line.label}: <Text bold>{line.value}</Text>
              {(line.topic?.items ?? []).length > 0 ? ` · ${(line.topic?.items ?? []).slice(0, DETAIL_ITEMS).map(i => i.title ?? i.id).join(', ')}` : ''}
            </Text>
          ))
        )}
      </Box>
    )
  })
}
