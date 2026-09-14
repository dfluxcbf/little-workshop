← [Independence & Self-Governance](07-self-governance.md) · deck 8 of 8 · back to [index](README.md)

# Core's Recovery, and Who Owns What Configuration

Two questions, answered together because they share one underlying idea:
a crashed or restarted part of the system should rejoin what's already
running, never reset it — and that same discipline is what makes shared
configuration safe without a permissions system to enforce it.

## Core can't supervise its own crash — so it doesn't try to

A crashed process cannot relaunch itself. Core's own recovery is the exact
same `systemd --user Restart=on-failure` primitive it configures for every
pillar, applied to its own unit too — no special, more fragile mechanism
for itself.

What *is* new: because every pillar is started via `systemctl --user start
<pillar>`, each one becomes systemd's child, never Core's. Core crashing,
and later restarting, never touches them — they just keep running.

---

## Reconnect, don't relaunch

The design risk was Core assuming, on restart, that it needs to start
everything from scratch. Instead:

```
Core restarts (crash, or first boot — same code path either way)
   → reads manifest.toml (survives the crash; it's a file, not memory)
   → for each enabled pillar: is its systemd unit already active?
        yes → probe its health endpoint → healthy? reconnect, don't touch it
        no  → start it fresh
```

A crash-and-restart and a first boot run the identical sequence — the only
difference is how much was already found running. Full diagram:
[`../docs/design.md` → Core's own recovery](../docs/design.md#cores-own-recovery--reconnect-dont-relaunch).

---

## Where configuration actually lives

Two tiers, kept apart on purpose:

| | Location | Who can write |
|---|---|---|
| Pillar's own settings | `~/.config/little-workshop/<pillar>/` | Only that pillar — the whole directory is already exclusive by path |
| Core's own bootstrap manifest | `~/.config/little-workshop/manifest.toml` | Core — sits beside the pillar directories, not inside one, since Core isn't a pillar |
| Shared per-project settings | `~/.config/little-workshop/projects/<project>/` | Whichever pillar's tag the filename carries |

Project files are the interesting case: one directory, shared by every
pillar with something to say about that project, so ownership has to live
in the filename: `<name>.<owner-tag>.<format>` — `retention.policy.json`,
`manifest.deployment.toml`. Full tag table:
[`../docs/architecture.md` → Configuration & project data](../docs/architecture.md#configuration--project-data).

---

## Read anything, write only your own

Any pillar may read any project file. Only the owning pillar may write it.
Needing a value *changed* that you don't own is a request to its owner's
Pillar Interface — never a direct write. Configuration establishes a
connection; it never carries the conversation. That's what interfaces are
for.

---

## Two guarantees, not one — stated honestly

- **One writer per file** — mechanically enforced. A shared client library
  is the only sanctioned way to touch the project files directory, and it
  refuses a mismatched write. This is a hard guarantee.
- **No two files meaning the same setting** — *not* mechanically provable
  in general. Nothing can verify two differently-named values encode the
  same concept from the outside. This is a design discipline (define a
  setting once, read it from its owner, never keep your own copy) backed
  by a best-effort check: matching key names across two owners' files get
  flagged for a human, not silently merged or silently allowed.

Neither guarantee needed OS-level filesystem permissions — every pillar
runs as the same host user, so there was never a UID boundary to lean on.
The same reasoning already behind this project having no multi-tenant
auth model at all.

---

Back to [index](README.md) · or revisit [2 — The Pillar Architecture](02-pillar-architecture.md)
