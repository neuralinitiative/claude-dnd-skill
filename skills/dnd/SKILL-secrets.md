# Knowledge Registry — who knows what

*Loaded on demand (see SKILL.md → Knowledge Registry). The gate rule itself lives in SKILL.md and always
applies; this file holds the procedures for the registry file `campaigns/<name>/secrets.md`.*

You know everything in the campaign files. The world does not. Facts in `state.md`, `npcs-full.md`, character
sheets and session logs are **DM knowledge**, not world knowledge: a fact being written down never means an
NPC knows it. The registry records, for each sensitive piece of information, which characters in the fiction
actually know it, and how they learned it.

Typical leaks it prevents:
- a disguised or shapechanged PC (a changeling in human form, an alias, a hidden patron) whom NPCs treat
  according to their true nature;
- an NPC who knows about an arrangement made by letter, in private, or in another city, with no way of
  having heard of it;
- a villain who reacts to what only one witness saw, or to a plan the party only discussed among themselves.

## The file

`campaigns/<name>/secrets.md`, from `${CLAUDE_SKILL_DIR}/templates/secrets.md`. Written in the table's
language. Two parts:
- **Cover stories**: what the world sees instead of the truth. An NPC who isn't in the matching entry's
  *Known by* perceives only the cover.
- **Secrets**: one entry per piece of information, with *Truth*, *Known by* (name, session, how), *Suspected
  by*, *Must not learn it*, *If revealed*.

What deserves an entry: anything whose spread would change how an NPC acts. That covers:
- PC secrets (true identity or nature, a hidden past, a patron, a curse);
- private party plans and deals;
- arrangements made at a distance (letters, envoys, a representative at a trial);
- an NPC's hidden motive or allegiance;
- quest truths the party has uncovered;
- anything the player asks to keep hidden.

Not every fact needs one: public events, what everyone in town saw, and things nobody would hide stay out.

## At load

If `secrets.md` exists, read it in full at `/dm:dnd load` (it is the gate's data, so it must be in context
before any NPC speaks). Keep it short: entries in *Revealed / public* can be trimmed to one line.

If it doesn't exist and the campaign already has sessions, offer once (in the table's language) to build it
with `/dm:dnd secret init`. If the player declines, the gate rule in SKILL.md still applies from memory.

## The gate: checking before an NPC speaks

Before an NPC states, uses, hints at or **reacts to** a fact that is in the registry, or one that is clearly
private even if not yet registered, answer one question: **how would this character know it?**

1. **Listed in *Known by*** → it knows; it acts on it in character (it may still choose to hide that it knows).
2. **Listed in *Suspected by*** → hints, probing questions, a wary look, a wrong guess. Never a flat statement
   of the truth.
3. **Not listed** → it doesn't know. It acts on the cover story and on what it can perceive right now.
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
  → add an entry with *Known by* = those present.

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

Present the batch as a numbered list, one line each (`+ Dace → Known by: valdrin-advocate (S9, network (Zhentarim))`)
and ask: *apply all / pick / skip*. Never write proposals silently. If `secrets.md` doesn't exist, skip this
step unless the session created a secret worth registering; in that case offer `secret init`.

## `/dm:dnd secret <init|add|reveal|who|check|list> [...]`

- **`init`**: build `secrets.md` for an existing campaign. Read the following:
  - `characters/*.md`, for PC secrets, disguises, aliases and hidden pasts;
  - `npcs-full.md` (`Secret:` fields and hidden goals);
  - `state.md` (`## Live State Flags`, `## Current Situation`, `## Recent Events`, `## Continuity Archive`,
    faction moves);
  - `session-log.md` / `recent-events-archive.md` as needed.
  
  For each sensitive fact, work out who in the fiction learned it, and when and how, from the logs. When the
  logs don't say, put the NPC in *Suspected by* or leave them out, and mark the line `(?)`. Never guess
  someone into *Known by*. Show the draft to the player, apply their corrections, then write the file.
- **`add <id> "<truth>" [known:<A,B>] [kind:<k>]`**: new entry. *Known by* defaults to the PCs present.
- **`reveal <id> to <A,B> [how]`**: add them to *Known by* (the current session and `how`, default `told`).
  `reveal <id> public [how]` moves it to *Revealed / public*.
- **`who <id>`**: list *Known by*, *Suspected by* and *Must not learn it* for one entry. Out of character.
- **`check <npc> [topic]`**: out of character. List what the NPC knows, suspects, and must not learn, and
  which cover stories it sees. Run it before a big scene with that NPC.
- **`list`**: every entry id and label, with a count of who knows it.

A `pc` entry belongs to that PC's player. At a table with several players, other PCs may not know it either:
list them in *Known by* like anyone else, and don't print a `pc` entry's *Truth* in `who`, `check` or `list`
output unless its player asked.
