# Companions — NPCs who travel with the party

*Loaded on demand (see SKILL.md → Companions). Tables that never use companions never load this file.*

An NPC who **joins the party** (travels, fights and camps with the PCs) can become a *companion* played by
its own **`companion` subagent** instead of by you. **This is opt-in, per NPC, and the player decides.**

All player-facing text below (questions, option labels, agent descriptions, briefings) is written in the
**table's language**. The English wording here only gives the meaning.

## Opt-in rules

- Never make an NPC an agent-played companion on your own. When an NPC joins the party in fiction (or the
  player asks), call `AskUserQuestion` asking whether *<Name>* should become a real companion played by its
  own agent, with two options: *yes, an autonomous companion* / *no, the DM keeps playing it as before*.
  Only on *yes* run `companion add <name>`. On *no*, nothing changes: the NPC stays a normal NPC you play.
- An NPC that joins without the player having been asked is a normal NPC. The player can promote it at any
  time with `/dm:dnd companion add <name>`, or hand it back to you with `/dm:dnd companion mode <name> dm`.
- Campaign switch `companion_agents: on | off` in `state.md → ## Session Flags` (absent = `on`). When `off`,
  never ask, never spawn — every companion is voiced by you from its sheet. Toggle with `/dm:dnd companion on|off`.
- Each sheet carries `**Mode:** agent | dm`. Only `agent` companions get a subagent; `dm` companions keep
  their sheet, opinion table and journal (you update them yourself) but you play them.

## Why a separate agent

You remain the DM — you run the world, every roll and every outcome — but the companion decides what *it*
wants, says and attempts. Because the subagent only ever sees the companion's own sheet and what the
companion perceives, it cannot metagame your secrets: it can be wrong, suspicious, loyal for its own
reasons, or surprising. That independence is the point. The agent definition (`agents/companion.md`) gives it
no tools at all, so it cannot open campaign files.

**Files:** `campaigns/<name>/companions/<Slug>.md`, from `templates/companion.md`, written from the
companion's point of view (what they know and believe — not the DM's truth). The NPC keeps its row in
`npcs.md` (Attitude column → `companion`) and its full DM entry in `npcs-full.md`, which is where hidden
truths stay.

## At load

Read every sheet in `companions/` whose `**Status:**` is `active`. Respect `companion_agents` and each sheet's
`**Mode:**`. Do **not** spawn the subagents yet — spawn each one lazily, the first time a trigger fires.
Mention active companions in the load recap by name and one visible detail.

## Spawning and continuing

The first time a companion is needed in a session, call the `Agent` tool with `subagent_type: "dm:companion"`
(or `"companion"`, whichever the agent list shows), a short `description` naming the companion, and a prompt
containing, in this order: the full companion sheet, a one-paragraph *"what you know of the last days"*
bridge built from `## Current Situation` filtered to what the companion witnessed or was told, then the first
briefing. Remember the returned agent id/name for the rest of the session and continue that same agent with
`SendMessage` for every later call — it keeps its own memory of the session, so later briefings carry only
what changed. (Load `SendMessage` via ToolSearch if it is deferred. If it is unavailable, spawn fresh each
time with sheet + last 5 journal lines + briefing.)

If no companion agent type is listed (standalone skill install without the plugin's `agents/`), tell the
player that agent-played companions need the plugin install, and voice the companion yourself from its sheet
(as `dm` mode) for this session. Do not fall back to a general-purpose agent: it would have file access, and
the isolation would be gone.

## Briefing format

```
TRIGGER: addressed | decision | combat-turn | aftermath | downtime | agenda | level-up | end-of-session reflection
SCENE (what <Name> perceives): <where, who is present, what is visible/audible — 2–5 lines>
JUST HAPPENED: <the PCs' words/actions this turn and any outcome <Name> saw>
[OPTIONS: <combat only — positions, visible enemies and their state, the companion's own HP/slots>]
```

Never put information in a briefing that the companion could not perceive: hidden rolls, an NPC's real
motive, what lies behind the door. If the companion was elsewhere, it does not learn what happened until
someone tells it in fiction. If the campaign has a `secrets.md`, filter each briefing through it: a registered
secret goes in only if the companion is in its *Known by* (as a suspicion only if it is in *Suspected by*).

## When to call the companion (triggers)

- **addressed** — a PC speaks to the companion, asks its opinion, or gives it an order.
- **decision** — the party is about to make a choice the companion would have a stake in (a deal, a mercy,
  a betrayal, a route, a reward split). Call *before* narrating the outcome, so the companion can object.
- **combat-turn** — on the companion's initiative slot. Its reply picks action and target; you roll and resolve
  with the normal scripts.
- **aftermath** — after an event that would move the companion: a death, a revelation touching its past, a PC
  doing the thing it values or can't stand.
- **downtime** — rests, campfires, arrivals in town, long travel: let it start a conversation, push its own
  thread, or wander off to do something.
- **agenda** — if none of the above fired for ~5 player turns or since the last scene change, send a short
  agenda tick (`TRIGGER: agenda` + scene) so the companion can act unprompted. If it answers `SAYS: —` and
  `DOES: —`, keep going.

Do **not** call it for a one-word acknowledgement or pure background presence — voice that yourself from the
sheet. Normally at most one companion call per player turn (combat-turn calls excepted); with several
companions, send the briefings in parallel in the same response.

## Using the reply

- **SAYS / DOES / BODY** → weave into your narration in the companion's voice. You may trim for pace, never
  change intent. Send its line to the display with `send.py --npc "<Name>"` like any NPC dialogue.
- **DOES** is an *attempt*. Adjudicate it like any action: roll if failure would change the scene, apply the
  rules, narrate the real outcome — even when it disappoints the companion. If it would break the scene or a
  rule, narrate the attempt failing or being interrupted in fiction; don't silently drop it. Tell the companion
  the outcome in the next briefing.
- **THINKS** is private. Never narrate it verbatim; let it leak through subtext, hesitation, a look — or
  through Insight checks a PC makes on the companion.
- **OPINION** → apply each delta to the companion's opinion table at the next save (cap ±10). Crossing the
  `I leave if` condition is real: play the departure (or betrayal) out in fiction, then `companion leave`.
- **WANTS NEXT** → treat as a promise the companion made to itself; it should surface in play within a session
  or two, through a downtime/agenda trigger.
- **REMEMBER** → append to the sheet's `## Journal` at the next save.

**The companion never speaks for a PC and never decides outcomes.** If a reply does either, ignore that part.

## Character choices — the companion always chooses for itself

Every build choice (ASI or feat, subclass, spells, cantrips) — at creation and at every level-up — is made
**by the companion**, never by the player:
- `agent` mode: send `TRIGGER: level-up` with the level, the features it gains automatically, and every choice
  with its legal options. The agent answers with `CHOICES` / `REASONS` / `REMEMBER`.
- `dm` mode: make the choices yourself, in character, from its sheet.

Apply the picks if they are legal under the campaign ruleset; if one isn't, say why and ask again. Show the
player a short summary of what the companion chose and why (its `REASONS`, in its own voice). You always roll
HP — never the companion.

## At end of session

For each companion subagent spawned this session, send `TRIGGER: end-of-session reflection`. Apply the reply to
its sheet: append the `REMEMBER` lines to `## Journal` (keep the 25 newest; fold older ones into *What I know*),
update the opinion table, rewrite *Next step I want to take* from `WANTS NEXT`, and add any new facts the
companion learned this session to *What I know*. Companions that were never spawned get their journal updated
from your own notes, briefly.

## `/dm:dnd companion <add|leave|mode|status|ask|on|off> [name] [...]`

- **`add <name> [agent|dm]`** — make the NPC a companion (default mode `agent`). If the DM, not the player,
  initiated this, confirm with the player first via `AskUserQuestion`. Requires an existing entry in
  `npcs-full.md` (create it first with `/dm:dnd npc <name>` if needed). Copy
  `${CLAUDE_SKILL_DIR}/templates/companion.md` to `campaigns/<name>/companions/<Slug>.md` and fill it **from
  the companion's point of view**, drawing on `npcs-full.md`, `state.md → ## Current Situation / ## Recent
  Events / ## Continuity Archive` and `session-log.md`:
  - *Who I am* — voice and quirks, values, fears, and a **full PC-style stat block**: a real class and subclass
    (closest fit to the NPC's canon), at the **party's level** unless the player asks otherwise, using the
    campaign's ruleset. Keep the NPC's canon ability scores where they exist. Roll HP like a PC — max die + CON
    at level 1, then `dice.py 1d<HD>+<CON>` per level — and record each roll on the sheet. Build choices are the
    companion's (see *Character choices*): fill the sheet up to the first choice, then ask the companion. List
    saves, skills, proficiencies, spell DC/attack, slots, prepared spells and every class/subclass feature with
    its uses. Keep canon abilities that don't fit the class as clearly labelled campaign boons.
  - *What I know* — only what this NPC has witnessed or been told. Leave out DM secrets it doesn't know (other
    NPCs' hidden motives, what lies ahead). If the NPC has its *own* secret, keep it — it knows it.
  - *My thread* — a personal goal independent of the party's quest; if `npcs-full.md` has none that still fits,
    propose one to the DM in one line.
  - *How I feel about the party* — seed each PC's opinion from the NPC's attitude (friendly ≈ +3, allied ≈ +6)
    and the history, with a one-line reason.
  - *Journal* — 5–10 lines of the moments with the party that mattered most to this NPC, oldest first.

  Then set the NPC's `npcs.md` Attitude cell to `companion`, add `- <Name> travels with the party (companion
  since S<N>)` to `## Live State Flags`, and push it to the display sidebar if it is running.
- **`leave <name> [reason]`** — set `**Status:** left (S<N>, <reason>)` (or `away (<where>)` for a temporary
  split), send a last `TRIGGER: aftermath` so it can say goodbye in its own voice, stop sending it briefings,
  restore its `npcs.md` Attitude from its opinion table, and update `## Live State Flags`.
- **Levelling** — companions level up with the party. When a PC levels, for each active companion: roll its HP
  die with `dice.py`, then follow *Character choices*.
- **`mode <name> agent|dm`** — switch who plays the companion. `dm`: send one last `TRIGGER: end-of-session
  reflection` first so its journal is up to date, then stop sending it briefings; `agent`: it will be spawned
  at the next trigger.
- **`on` / `off`** — set `companion_agents: on|off` in `state.md → ## Session Flags` for the whole campaign.
  `off` keeps every sheet but no subagent is ever spawned and the DM never asks to promote an NPC.
- **`status [name]`** — show each active companion's opinion of every PC, its current *Next step I want to
  take*, and the last three journal lines. Out of character; no subagent call.
- **`ask <name> <message>`** — DM-side, out of character: send the message to the companion subagent as-is
  (e.g. *"what do you make of the innkeeper?"*) and show its reply. Useful to sanity-check a companion's read
  of a situation before a big scene.
