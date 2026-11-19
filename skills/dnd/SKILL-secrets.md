# Knowledge Registry — who knows what

*Loaded on demand (see SKILL.md → Knowledge Registry). The gate rule itself lives in SKILL.md and always
applies; this file holds the procedures for the registry file `campaigns/<name>/secrets.md`.*

You know everything in the campaign files. The world does not. Facts in `state.md`, `npcs-full.md`, character
sheets and session logs are **DM knowledge**, not world knowledge: a fact being written down never means an
NPC knows it. Leaks rarely come from missing information; they come from having *too much* of it in context
when an NPC speaks. So the registry does two jobs:
- it records which characters in the fiction actually know each sensitive fact, and how they learned it;
- through the scene brief, it puts the **cover story** (what an NPC perceives) in front of you at the moment
  the scene's cast is set.

Typical leaks it prevents:
- a disguised or shapechanged PC (a changeling in human form, an alias, a hidden patron) whom NPCs treat
  according to their true nature;
- an NPC who knows about an arrangement made by letter, in private, or in another city, with no way of
  having heard of it;
- a villain who reacts to what only one witness saw, or to a plan the party only discussed among themselves.

## The file

`campaigns/<name>/secrets.md`, from `${CLAUDE_SKILL_DIR}/templates/secrets.md`. Field labels stay in English
(the brief script reads them); content is written in the table's language. One entry per piece of
information:
- *Truth*: the information itself;
- *Cover*: what those who don't know perceive instead;
- *Known by*: name, session and how they learned it;
- *Suspected by*, *Must not learn it*, *If revealed*.

What deserves an entry is anything whose spread would change how an NPC acts:
- PC secrets (true identity or nature, a hidden past, a patron, a curse);
- private party plans and deals;
- arrangements made at a distance (letters, envoys, a representative at a trial);
- an NPC's hidden motive or allegiance, once someone besides that NPC knows it;
- quest truths the party has uncovered;
- anything the player asks to keep hidden.

Public events, what everyone in town saw, and things nobody would hide stay out. An NPC's own `Secret:` in
`npcs-full.md` doesn't need an entry while that NPC is the only one who knows it.

## The scene brief

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/secret_registry.py brief --campaign <name> --present "<NPC>,<NPC>,<companion>"
```

For every active entry, it prints the characters present who do **not** know it, with the cover they
perceive. It also prints who only suspects it, and who must not learn it along with what happens if they
do. List every named NPC and companion in the scene; PCs can be left out unless a PC secret is hidden from
another PC.

Run it:
- **at load**, for the scene the session opens on, next to `graph scene-context`;
- **every time the cast of a scene changes**: a new location, an NPC joining or leaving the conversation,
  the start of a social scene.

Keep its output in mind for the whole scene, and voice those NPCs through the cover. If the script prints
nothing to flag, nothing present is hidden from anyone.

## The gate: checking before an NPC speaks

Before an NPC states, uses, hints at or **reacts to** a fact that is in the registry, or one that is clearly
private even if not yet registered, answer one question: **how would this character know it?**

1. **Listed in *Known by*** → it knows; it acts on it in character (it may still choose to hide that it knows).
2. **Listed in *Suspected by*** → hints, probing questions, a wary look, a wrong guess. Never a flat statement
   of the truth.
3. **Not listed** → it doesn't know. It acts on the *Cover* and on what it can perceive right now.
4. **Not listed, but a plausible path exists** (an informant, a spy network, a scrying, a gossiping servant,
   a letter that passed through its hands) → don't assume it. Decide in the open:
   - roll for it if it's uncertain (`dice.py --silent`, or an oracle check);
   - or ask yourself whether that path was actually set up in play.
   
   If the NPC learns it, **write the entry first** (*Known by*: name, session, `network (<which>)` or how),
   then play it. The player can always ask "how does she know?" and must get a real answer.

Perception follows the cover:
- A disguised, shapechanged or illusioned PC is perceived **as they appear**. NPCs react to the human form,
  the alias, the face they see. Only a check that could pierce the disguise (Insight, Investigation, truesight,
  a tell the PC let slip) can move an NPC to *Suspected by*.
- Players' out-of-character talk, the player's own notes and the DM's narration of a PC's thoughts are never
  heard in the fiction.
- Only those present hear a conversation. A private conversation is private unless someone is eavesdropping,
  and then roll for it: a hidden Perception or Stealth contest.

## Updating the registry live

When information spreads in the fiction, update `secrets.md` at the same moment, as part of the
continuity micro-save:
- **Revealed in a scene** → add **everyone present** who could hear or see to *Known by* (`saw` / `told by <X>`).
  Include hidden listeners if they succeeded, and companions in the party.
- **Passed on off-screen** (an NPC reports to its faction, a letter arrives, a rumour travels) → add the
  recipients with `told by <X>` / `network (<which>)` / `document`. Note it in the faction move that caused it.
- **Guessed** → add to *Suspected by* with what they suspect and why.
- **Now common knowledge** → move the entry to *Revealed / public* with the session and how it got out.
- **New sensitive fact** (a PC adopts a disguise, the party makes a private plan, a deal is struck in secret)
  → add an entry with *Known by* = those present, and its *Cover*.

## Companions

When `SKILL-companions.md` is in use, build each briefing and each sheet's *What I know* through the gate: a
companion receives a registered secret only if it is in *Known by*, and as a suspicion only if it is in
*Suspected by*. A companion that witnesses a reveal goes into *Known by* like anyone else.

## At end of session

Step of `/dm:dnd end` (after the save). Scan this session's narration for knowledge that moved and wasn't
registered live:
- reveals, confessions, documents read, eavesdropping;
- NPCs who reported to someone;
- disguises used or seen through;
- private plans made.

Present the batch as a numbered list, one line each (`+ Orrin → Known by: envoy-letter (S9, network (thieves' guild))`)
and ask: *apply all / pick / skip*. Never write proposals silently. If `secrets.md` doesn't exist, skip this
step unless the session created a secret worth registering; in that case offer `secret init`.

## `/dm:dnd secret <init|add|reveal|brief|list> [...]`

- **`init`**: create `secrets.md` for a campaign that doesn't have one yet. Copy the template, then ask the
  player (`AskUserQuestion`, in the table's language):
  - whether any PC has a secret identity or nature, a hidden past, a patron or an allegiance, and what the
    world sees instead (*Cover*);
  - whether the party is keeping any plan, deal or arrangement private.
  
  For each answer, read the matching PC sheet (`characters/<PC>.md`) and `state.md → ## Live State Flags` /
  `## Current Situation`, and draft the entry with *Known by* = those the player names. Never guess anyone
  into *Known by*; if unsure, ask or leave them out. Don't seed entries from `Secret:` fields and don't mine
  the session logs. Show the draft, apply the player's corrections, then write the file. Later entries are
  added live as play reveals them.
- **`add <id> "<truth>" [cover:"<cover>"] [known:<A,B>] [kind:<k>]`**: new entry. *Known by* defaults to the
  PCs present.
- **`reveal <id> to <A,B> [how]`**: add them to *Known by* (the current session and `how`, default `told`).
  `reveal <id> public [how]` moves it to *Revealed / public*.
- **`brief [<names>]`**: run the scene brief for the given names, or for the current scene's cast, and show
  it out of character.
- **`list`**: `secret_registry.py list`, giving every active entry id and label with how many know it.

A `pc` entry belongs to that PC's player. At a table with several players, other PCs may not know it either:
list them in *Known by* like anyone else, and don't show a `pc` entry's *Truth* to the table unless its player
asked.

**Import:** at `/dm:dnd import` (gap-fill wizard) and at `/dm:dnd character new`, ask the same PC question as
`init`. If any PC has a secret, create `secrets.md` (or add to it) with those entries.
