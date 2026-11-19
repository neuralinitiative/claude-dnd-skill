# Knowledge Registry — <campaign>

*DM-only. Who in the world knows each piece of sensitive information. Everything in the campaign files is
known to the DM; this file says who **in the fiction** knows it. An NPC only states, uses or reacts to an
entry if it is listed under **Known by** (or hints at it if under **Suspected by**); everyone else perceives
the **Cover**. See `SKILL-secrets.md`. `scripts/secret_registry.py brief` reads this file: keep the field
labels in English, write the content in the table's language.*

*Entry ids are short slugs (`kael-changeling`, `party-plan-ambush`). Kinds: `pc` (a PC's hidden identity,
nature, past, patron or allegiance), `party` (a plan, a deal or an arrangement the party keeps private),
`quest`, `npc`, `faction`, `world`. "How" is one of: saw, told by <X>, overheard, deduced, network (<which>),
document. Separate people with `·`.*

## Secrets

### <entry-id> — <one-line label>
- **Kind:** pc | party | quest | npc | faction | world
- **Truth:** <the information itself, one or two sentences>
- **Cover:** <what those who don't know perceive instead — "a human woman named Mara" — or *none*>
- **Known by:** <Name> (S<N>, how) · <Name> (S<N>, how)
- **Suspected by:** <Name> (S<N>, what they suspect and why) — or *none*
- **Must not learn it:** <Name / faction> — or *none*
- **If revealed:** <consequence> — or *none*
- **Notes:** <optional — limits, who could leak it, a document that carries it>

## Revealed / public
*Entries that became common knowledge. Move them here with the session and how it got out; the brief skips
them, and NPCs may know them if they plausibly heard.*
