# Campaign: <name>
**Created:** <date>  **Last session:** —  **Session count:** 0  **Ruleset:** 2014

## Current Situation
- **Location:**
- **In-world date/time:**
- **Party:** <Name> — <Race> <Class> <Level> | HP X/X | AC X | <resources>
- **Party status:**

## World State
- **In-world date:** <Day, Month, Year — canonical source; keep in sync above>
- **Season:** <current season>  **Weather:** <current conditions>
- **Threat arc stage:** 1 — Now
- **Faction states:**
  - <Faction Name>: <one-line current disposition and activity>
  - <Faction Name>: <one-line>

## Active Quests
*(none yet)*

## Open Threads & Rumours

## Faction Moves
*Updated at the end of each session — what each active faction did while the party was occupied.*
*(none yet)*

## Recent Events
*(Session 1 pending)*

## Active Combat
*(none)*

## Live State Flags
*Structured facts designed to survive context compaction. Re-read this section before any recap, status summary, or claim about cover, NPC stance, or faction standing. Update at every /dnd save.*

**Cover:**
*(none established)*

**Faction stances** *(only list factions with non-neutral standing toward the party)*:
*(none established)*

**NPC dispositions** *(only list NPCs with changed or notable standing)*:
*(none established)*

## Campaign Arc
*(sandbox campaigns: set `type: sandbox` — no arc tracking)*
*(structured campaigns: populated by /dnd import — use structured format)*
*(improvised campaigns: auto-generated at /dnd new — use dynamic format)*
```yaml
# --- DYNAMIC ARC (improvised campaigns, auto-generated at /dnd new) ---
type: dynamic
arc_number: 1          # increments each time /dnd arc new generates a successor arc
generated: "<date>"
revised: null

theme: "<one sentence — what this story is ultimately about, not what happens but what it means>"
resolution: "<committed endpoint shape — not specific events, but the emotional/thematic truth if the party succeeds>"

acts:
  - act: 1
    title: "Setup"
    drive: "<what the party wants or needs at the start>"
    beats:
      - id: "1a"
        label: "<Inciting Incident>"
        what_changes: "<before vs. after — what's fundamentally different once this lands>"
        world_pressure: "<specific faction/NPC move that makes this beat feel inevitable>"
        status: current     # current | complete | skipped
      - id: "1b"
        label: "<Complication>"
        what_changes: "<what the party learns that makes the problem bigger or stranger than it first appeared>"
        world_pressure: "<what creates this pressure>"
        status: pending

  - act: 2
    title: "Confrontation"
    drive: "<what the party is now pursuing — may differ from act 1>"
    beats:
      - id: "2a"
        label: "<Midpoint Shift>"
        what_changes: "<the thing they thought was true / the goal they thought they had changes>"
        world_pressure: "<what forces this realization>"
        status: pending
      - id: "2b"
        label: "<All Is Lost>"
        what_changes: "<a genuine setback — something fails, is lost, or collapses>"
        world_pressure: "<what drives this failure>"
        status: pending

  - act: 3
    title: "Resolution"
    drive: "<what the party now understands and is fighting for>"
    beats:
      - id: "3a"
        label: "<Final Confrontation>"
        what_changes: "<the decisive moment the campaign turns on>"
        world_pressure: "<the escalated form of the original threat>"
        status: pending
      - id: "3b"
        label: "<Resolution>"
        what_changes: "<what's different about the world and the characters after>"
        world_pressure: "<what factions/NPCs do with the outcome>"
        status: pending

current_act: 1
current_beat: "1a"

outstanding_beats:
  - "1a"
  - "1b"
  - "2a"
  - "2b"
  - "3a"
  - "3b"

steering_notes: >
  <Active guidance — what world pressure to apply to reach the current beat without forcing it.
  Update at each /dnd end when a beat advances or needs active steering.>

revision_log: []

# --- STRUCTURED ARC POINTER (imported campaigns, populated by /dnd import) ---
# Structured campaigns keep only this lightweight window inline. The full
# act/chapter tree lives in arc.md so it stays out of the hot path at load.
# Read arc.md only when advancing chapters or answering a broad-arc question.
# type: structured
# source: "<title>"
# structure: linear      # linear | hub-and-spoke | faction-web
# arc_file: arc.md
# current_act: 1
# current_chapter: "1.1"
# current_chapter_detail:
#   id: "1.1"
#   title: "<chapter name>"
#   location: "<primary location>"
#   source_ref: "source/1.1.md"
#   key_beats: ["<beat>", "<beat>"]
#   telegraph_scene: "<setup scene>"
# next_chapter: "1.2"      # so the DM can foreshadow without loading arc.md
# outstanding_beats: ["<beat>"]
# steering_notes: >
#   <How to guide players toward outstanding beats without forcing.>
```

## Arc History
*(populated by /dnd arc new when a completed arc is succeeded by a new one — one entry per completed arc)*
*(leave empty until the first arc completes)*

## Session Flags
*(tutor_mode, autorun, autorun_interval, tts_voice, sfx_languages, autosave — session-scoped flags set via /dnd commands or by the display companion)*
*(autosave: on|off — default on. Governs the behind-the-scenes continuity checkpoint (Live State Flags + graph + session tail). Toggle with /dm:dnd autosave on|off.)*

## DM Notes (hidden from players)
