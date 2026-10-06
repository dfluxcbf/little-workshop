// The little-workshop status mod's state contract (installed as types/index.d.ts).

// One entry behind a count: a Job, a task, a scheduled task, a policy, an incident or a pillar.
export type Item = {
  id: string
  title?: string | null
  at?: string | null
  href?: string
  status?: string | null
  error?: string | null
  crash_count?: number
}

// A topic of the Dashboard's /status.json: `count` null when its pillar did not answer.
export type Topic = { count: number | null; items: Item[]; hours?: number; of?: number | null }

export type Status = {
  project?: string | null
  generated_at?: string
  server?: { known: boolean; running: boolean; listening: boolean; unit?: string | null } | null
  running?: Topic
  tasks_attention?: Topic
  failed_jobs?: Topic
  scheduled?: Topic
  policies?: Topic
  incidents?: Topic
  dead_pillars?: Topic
  unavailable?: string[]
  error?: string
}

export type Checkout = { project: string; version: string; branch: string }

declare module 'claude-code' {
  interface PluginState {
    'little-workshop-status': { status: Status | null; checkout: Checkout | null }
  }
}
