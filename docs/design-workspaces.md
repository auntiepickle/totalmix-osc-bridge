# Workspaces, snapshots and macros — design sprint (draft for review)

Status: DRAFT 2026-09-14, nothing implemented. Review in the tracking issue;
this file is the record of what we decide.

## The problem, as seen on the live rig

TotalMix identifies a Quick Select workspace by its slot (1-30); the name is
a label in `rme.totalmix.preferences.xml`, and each slot's eight snapshot
names live in `presetN.tmws`. A workspace can also be loaded from any `.tmws`
file, in which case it has a name (the file's base name) but no slot. People
save, rename and rotate workspace files into slots as sets evolve.

The bridge binds everything to names only: `ufx2_snapshot_map.json` maps
names to slots and snapshot names, and every macro's `workspace` / `snapshot`
is a name. That map is hand-maintained (or scraped once), so it drifts:

- Slot 7 was `Pill_setup` with four snapshots; it is now `dub_music` with
  seven. The map still says `Pill_setup`. Three macros bind to that name.
- The header now follows TotalMix (#30: the agent sends the window title),
  so it shows `dub_music` correctly, but the snapshot is `snap_4` because no
  map entry knows dub_music's snapshot names.
- The other five slots happened not to change - this time.

Wanted, in the user's words: bind by name when the name is the point, bind
by slot when the position is the point, load the next workspace or file in a
set and get its macros with it, and never have the bridge silently rewrite a
mapping.

## Principles

1. **Two binding modes, chosen per reference.** A workspace or snapshot
   reference is either *by name* or *by slot*. Plain strings stay names, so
   every existing mappings.json is valid unchanged.
2. **TotalMix's own files are the truth; the agent delivers them.** The
   agent on the TotalMix machine already reports the window title. It also
   ships a *catalog* (slot -> name, snapshot names per slot, and the loaded
   file's snapshot names) whenever those files change. The bridge derives
   what the hand-maintained map used to hold.
3. **Drift is surfaced, never guessed.** Anything that does not resolve is
   flagged on its card with a suggestion. No automatic rewrite of mappings.
4. **The workspace is the context.** The rack shows the active workspace's
   macros first. Loading a workspace or a file in TotalMix lights up its set.
5. **Honest about what OSC cannot do.** A file workspace cannot be loaded
   remotely (no OSC command exists); its macros wait for the user to load it.

## Binding modes

| Mode | Meaning | Survives | Breaks on | Switchable |
|---|---|---|---|---|
| name | "the workspace called dub_music, wherever it is" | moving it to another slot; file workspaces | rename (flagged, rebind suggested) | while the name occupies a quick slot |
| slot | "whatever is in slot 7" | renames, rotating a different file into the slot | emptying the slot (flagged) | always |

Snapshots inside a workspace get the same choice: by name (`Reset`) or by
position (slot 4). Name mode survives reordering within the workspace; slot
mode survives renaming a snapshot.

### Data shapes (backward compatible)

```jsonc
// today, unchanged, means "by name"
"workspace": "dub_music",
"snapshot": "Reset",

// explicit slot binding
"workspace": {"slot": 7},
"snapshot": {"slot": 4},

// explicit name binding is also allowed (same as the plain string)
"workspace": {"name": "dub_music"}
```

One reference has exactly one of `name` / `slot`. The editor stores whatever
mode the user picked; the card renders both halves in either mode, e.g.
`slot 7 · dub_music`, taken live from the catalog.

Macro scope (see open questions): a macro whose `workspace` is unset is
*global*; otherwise it belongs to that workspace's set.

## Sources of truth

| What | Source | Live? | Notes |
|---|---|---|---|
| Active workspace name | TotalMix window title, sent by the agent with its heartbeat (#30) | yes, < 1 s | quick name, or `.tmws` path for a file workspace |
| Active snapshot slot | Global OSC `/snapshot/load/N` (0 off / 2 active / 3 modified) | yes | slot only, never a name |
| Slot -> workspace name | `%LOCALAPPDATA%\TotalMixFX\rme.totalmix.preferences.xml` (`PresetNameN`) | on save | agent watches mtime |
| Snapshot names per quick slot | `presetN.tmws` (`SnapshotName 0..7`) | on save | agent watches mtime |
| Snapshot names of a file workspace | the `.tmws` at the title's path | on load/save | agent reads it when the title names a file |
| Workspace switch | `/loadQuickWorkspace N` (classic remote) | - | quick slots only |
| Snapshot recall | `/3/snapshots/<osc index>/1` | - | within the loaded workspace |

Not usable: `LastPresetSel` (written on save only), `last.*.xml` (written on
exit, carries no workspace id), the Global status block (device, connection,
dsp only). Verified 2026-09-14.

### The catalog feed

`POST /api/device/catalog` from the agent, on change and on connect:

```jsonc
{
  "host": "BONE",
  "quick": {"1": {"name": "Blank", "snapshots": ["Default", "DAW", "Messin around", "Work", "fresh", "Gaming", "DAW", "909 Idea"]},
            "7": {"name": "dub_music", "snapshots": ["Default", "", "dub_final", "Reset", "dub_wet", "dub_pre_wet", "relax", "lastthingdid"]}},
  "file": {"name": "dub_final", "path": "<as shown in the title>", "snapshots": ["..."]},   // when a file workspace is loaded
  "ts": 1789400000
}
```

The bridge persists the last catalog next to the other state files and
derives `snapshot_map` from it (same shape as today, so every existing
consumer keeps working). `ufx2_snapshot_map.json` becomes a generated export
and the fallback for a setup without an agent; the scraper is retired. An
empty snapshot name is `Empty N`, as the scraper did.

The agent parses the XML with plain string scanning (`<val e="PresetName7"
v="dub_music"/>`); no XML library. Paths in the title are the user's own
(the `Z:` share in the reference setup) and are never assumed.

## Bridge behaviour

**Resolve a workspace reference.** name -> catalog lookup (exact, then
case-insensitive) -> slot, or "file workspace, not switchable", or *unknown*.
slot -> catalog -> name for display, or *empty slot*.

**Resolve a snapshot reference** within the resolved workspace: name -> slot
via that workspace's list, or *unknown*; slot -> name for display, or
*empty*.

**Active context.** The title gives a name; the catalog gives its slot. A
macro is *active* when its workspace reference resolves to the active
workspace (by name or by slot, whichever it uses). Global macros are always
active.

**Firing a non-active macro** keeps today's behaviour (switch first, then
run) when the target is a quick slot; for a file workspace the macro
refuses with a clear reason ("load dub_final in TotalMix first").

**Drift flags** (per card, persisted with `macro_health`): workspace name
unknown (suggest the slot that last carried it, if the catalog history has
it), slot empty, snapshot unknown/empty. Suggestions are one click to apply;
nothing applies itself. A rename detected by slot continuity can also offer
"copy this set to <new name>" for the performance case.

**MQTT.** `totalmix/workspace` keeps the slot payload (Home Assistant
compatibility) and gains `totalmix/workspace/name`; the snapshot topic gets
the same treatment.

## UI

- **Rack**: three groups - *this workspace* (active set), *global*, and a
  collapsed *other workspaces* section. Loading a workspace or file in
  TotalMix regroups live.
- **Editor**: the workspace and snapshot pickers get a name / slot toggle;
  the option list comes from the catalog (names and `slot N · name`); free
  text stays possible for a workspace the catalog has not seen yet.
- **Card**: drift chip with the reason and the suggested fix.
- **Header**: already shows the reported workspace; adds the slot when known
  and "file" for a file workspace.

## Phases

1. **Catalog feed.** Agent (Windows console + tray): watch the two file
   kinds, post the catalog. Bridge: accept, persist, derive `snapshot_map`,
   log changes. Fixes today's `snap_4` with no schema change. *Ships alone.*
2. **Binding modes.** Schema (`{"slot": N}` objects), resolution, editor
   toggle, card rendering of both halves. Existing files untouched.
3. **Drift flags + rebind/copy suggestions.** Includes catalog name history.
4. **Rack follows the workspace.** Grouping, file-workspace refusal reason.
5. **Docs, MQTT name topics, retire the scraper, export of the derived map.**

## Open questions for the review

1. Macro scope: can a macro belong to more than one workspace, or is it one
   workspace or global?
2. Rename: suggest a rebind only, or also offer "copy this set to the new
   name"?
3. Rack: other workspaces' macros hidden, dimmed, or collapsed?
4. Should the bridge keep a history of names per slot (for better rename
   suggestions), and for how long?
5. Setups without an agent (browser-only MIDI): keep the hand-maintained map
   as the catalog source, documented as the manual mode?
6. Immediate fix for slot 7 on the live rig: wait for phase 1, or rename the
   three macros' `workspace` to `dub_music` and refresh the map by hand now?

## Non-goals

- Loading `.tmws` files remotely (no OSC command; RME feature request).
- Editing TotalMix's files from the bridge or the agent - read only.
- Multi-device catalogs; one agent per TotalMix machine is the model.
