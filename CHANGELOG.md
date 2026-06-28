# Changelog

All notable changes to the D&D 5e DM skill are documented here. The skill follows [semantic versioning](https://semver.org/) — `MAJOR.MINOR.PATCH` where MAJOR breaks an existing campaign or workflow, MINOR adds significant new capability, and PATCH fixes bugs without changing behavior.

The current installed version is recorded in the `VERSION` file at the repo root. Run `/dm:dnd update --check` to compare your local copy against `origin/main`.

Versions before **1.6.0** are reconstructed retroactively from git history; the dates reflect the commit each version is anchored on. Going forward, every release lands in the same commit as a `VERSION` bump and a CHANGELOG entry.

---

## [Unreleased]

## [2.3.0] — 2026-06-27 — Zero-LLM continuity tools: deterministic recap, solo oracles, scene bangs, one-shot graph apply

- **Precomputed session recaps (deterministic, zero-LLM).** A new `scripts/session_recap.py` snapshots party state — HP/temp/level/hit dice/death saves/conditions/concentration/exhaustion/inspiration/spell slots — to JSON and diffs two snapshots into a plain-English change line (*"Aldric: took 18 damage (30→12 HP); gained Poisoned; spent 2 level 1 slots."*). It reads the character-sheet markdown and merges live `tracker.json` conditions/concentration, computing the change set from data rather than asking the model to reconstruct it — recaps are the single thing an LLM is most likely to hallucinate (wrong HP, dropped facts). Wired as **`/dm:dnd recap snapshot`** (take at `/dm:dnd end`) and **`/dm:dnd recap diff`** (inject the line at `/dm:dnd load`).
- **Solo/improv oracles as rollable tools.** A new `scripts/oracle.py` ports a Mythic-style **chaos factor** (1–9, persisted in `state.md → ## Session Flags`), an Ironsworn-shaped **yes/no** oracle (likelihood + chaos modifier → verdict with `-and`/`-but` shades + d100), a Mythic **Random Event Focus** (d100 direction labels), and **scene-meaning** word pairs. Rolls are stdlib-random and seedable for reproducibility; no Claude API call. Exposed as **`/dm:dnd oracle chaos|ask|event|scene`**. These give the table a transparent dice backbone for improvised play instead of leaving every beat to model whim.
- **Open scenes with a bang (new DM standard).** SKILL.md Standard 13 directs the DM to open every scene *transition* with a hard question that forces an immediate choice — an NPC naming a price to accept or refuse now, a door slamming with the wrong footsteps behind it — instead of dead-air "what do you do?". Scoped to transitions (chapter break, new location, time skip, post-rest); continuation scenes mid-flow are exempt so pacing isn't churned. Faction moves logged under Standard 11 are the suggested raw material.
- **One-shot deterministic graph apply.** `campaign_graph.py extract --deterministic` gains `--apply` (with `--min-confidence` and `--no-auto-nodes`): it runs the zero-LLM verb-table extractor and writes proposals at/above the confidence threshold straight into `graph.json`, deduped and **idempotent** (re-running adds nothing). This makes a hands-off relationship sweep possible at `/dm:dnd save` without the two-step write-then-review-file dance; the interactive `extract-apply --review` path is unchanged for human-in-the-loop use. The apply logic is now shared between both paths.

## [2.2.2] — 2026-06-22 — Display input fixes — reading column clears PARTY INPUT, honest send-failure

- **Display: the PARTY INPUT box no longer covers the bottom of the narration.** The fixed input panel grows when it expands (or the editorial drawer opens, or the mobile keyboard raises the viewport). The reading column now reserves bottom space equal to the panel's live on-screen footprint via a `ResizeObserver`, so the last lines of narration always clear it.
- **Display: a failed player-input submit now says so.** If the browser→server POST fails after retries, the Stage button shows "Send failed — tap to retry" (the typed text and its `localStorage` cache are preserved, so a tap re-sends) instead of silently resetting to "Stage" — which previously read as "submitted but not acknowledged."

## [2.2.1] — 2026-06-18 — Multi-column PDF import (chapter segmentation fix)

- **Imported PDFs no longer collapse into one chapter.** Most published modules are two-column, and the extractor used `pdftotext -layout`, which preserves physical layout and **interleaves the columns** — scrambling reading order (keyed encounters came out 1, 2, 4, 3…) and burying section headers mid-line. Handed that, the import had nothing clean to segment on and lumped the whole book into a single chapter. PDF extraction now uses **PyMuPDF column-aware ordering**: text blocks are sorted into true reading order (left column then right, with full-width headers acting as band dividers), so section headers and keyed encounters come out clean and in sequence and chapter segmentation works on large modules. Verified on an 82-page, 38k-word anthology that previously collapsed.
- **PyMuPDF is the recommended import dependency** (`pip3 install pymupdf`). It is not a hard requirement: if it is unavailable the extractor falls back to `pdftotext` in **reading-order mode** (no longer `-layout`), which de-columns far better than the old default. Markdown/DOCX/TXT import is unaffected.

## [2.2.0] — 2026-06-18 — Lazy-corpus imports + behind-the-scenes autosave

- **Imported modules now load like dynamic campaigns.** Previously, `/dm:dnd import` distilled a source book into the campaign files and discarded the rest, but the distilled files for a full published adventure were large — and `world.md` (the whole quest/location bank) plus the entire act/chapter arc were read in full at every session load, so big modules pushed sessions toward compaction faster. Imports now keep the full module as a **lazily-loaded reference layer** instead: the act/chapter tree lives in `arc.md`, the quest seeds and adventure nodes in `world-nodes.md`, and the source text split per chapter under `source/<chapter-id>.md` with a `source-index.md`. None of these load at session start. `state.md → ## Campaign Arc` keeps only a lightweight pointer plus the current and next chapter; the DM pulls a chapter's source, nodes, and arc detail on demand — the same way it already reads a single NPC's full entry only when a scene centers on them. A long module now sits in context like a handful of chapters, not a whole book. `scripts/corpus_check.py` validates the layout at import time.
- **Behind-the-scenes autosave keeps continuity ahead of compaction.** A new continuity checkpoint flushes the compaction-resistant anchors — Live State Flags, the campaign graph, the session tail — at scene boundaries and on a turn cadence, so a context compaction never costs more than a turn or two of state. It writes only what `/dm:dnd save` already writes, just continuously, and never changes narration. The in-model micro-save works out of the box; an **opt-in** Stop hook (`scripts/install_autosave_hook.py`) adds a deterministic per-turn snapshot and cadence backstop. Toggle per campaign with **`/dm:dnd autosave on|off`** (default on); applies to every campaign type, not just imports.
- **Backward compatible — nothing to migrate.** Dynamic and sandbox campaigns are unchanged: no `arc.md`/`world-nodes.md`/`source/` is created for them, and their load path is byte-for-byte what it was. Campaigns imported before this release also keep working as-is — the load flow reads the new lazy files only when they exist and otherwise falls through to the previous full-read behavior. Re-importing the original source enables the lazy layer for an older campaign.

## [2.1.4] — 2026-06-06 — On-screen dice roller for no-phones games

- **On-screen dice roller for games without phones.** A fifth **Dice** launcher on the editorial rail slides out a drawer hosting the same `#dice-pad` players use on their phones (reel, die grid, advantage, modifier, label, prescribed-roll prefill) — reused 1:1, costs zero screen space when closed. Under `roll_mode: players`, the DM prescribes a roll via `send.py --dice-request … --wait`; the server now tracks which characters have a live phone bound (`/stream?character=`) and routes the roll there if so, or **auto-opens the on-screen drawer** if the target has no phone. A Settings **"Roll on screen"** toggle forces on-screen rolling regardless. Rolls go through the existing `POST /player-input/dice` (server-authoritative, resolves the request, releases the DM's `--wait`). Editorial-skin only; opt-in.
- **Hardened the v2.1.x preference endpoints.** `/narration-pref` and `/roll-pref` now sit behind the same `_token_ok()` + rate-limit gate as every other write endpoint (they previously accepted any LAN request). `/roll-pref` additionally validates the `character` field with `_char_ok()` — it's templated into a `[[<Char> roll mode: …]]` directive in the DM's prompt, so an unsanitized value was a prompt-injection surface.
- **Editorial NPC dialogue size reduced.** NPC pull-quotes dropped from `1.5em` to `1.2em` (line-height `1.42`) — the previous 1.5× jump over body text was overlarge and overflowed the reading column. Applies across all editorial-family skins.
- **Internal hardening.** Defensive `_prefill` guard + dev `console.warn` around the TV-view dice-routing wrap, and unit tests for `_phone_present()` (routing-presence semantics).

## [2.1.3] — 2026-06-04 — TCC-safe autorun wait

- **Autorun mode no longer breaks under macOS TCC.** The blocking autorun wait was a shell script (`autorun-wait.sh`) whose `echo > .autorun-session` redirect is silently blocked by macOS TCC when the data root lives under `~/Documents` — so autorun/taxi mode failed to drive the turn loop. Replaced with `autorun_wait.py`, a pure-python implementation doing the identical job (session invalidation, `autorun_interval` from `state.md`, waiting-indicator broadcast, 9-minute input-queue poll, `/queue/consumed` POST) using python file I/O, which TCC permits. Output contract is unchanged, so the DM turn loop is identical. The old shell script is retired.

## [2.1.2] — 2026-06-04 — Editorial character-sheet theming

- **Character-sheet modal fully themed for Editorial.** The sheet frame was already editorial, but its inner sections still rendered in Vellum gold on the paper canvas. Section titles and ability modifiers are now vermilion; ability scores, feature names, and feature text are ink (readable, no faint gold); spell/cantrip tags and dividers use subtle ink hairlines. The portrait icon drops its purple drop-shadow, and a 404'd class icon is removed (`onerror`) instead of leaving a broken-image box.

## [2.1.1] — 2026-06-04 — Enforced roll handling + move-received confirmation

- **Roll handling is now explicit and enforced.** The DM no longer silently auto-rolls a player's PC (the bug where it fell back to `dice.py` `[auto]` when the physical-dice server was down). Roll handling is chosen at game start — `/dnd new` and `/dnd load` now ask *"Dice rolls?"* — and stored as `roll_mode` in `state.md → ## Session Flags` (default `players`). SKILL.md hard-wires it: under `players` the DM calls for each PC d20 by name and **waits**, never rolling it for them; under `auto` it rolls PCs openly with full math. Initiative and all NPC/monster rolls stay DM-rolled.
- **Per-player roll override.** A `Rolls` toggle in the phone Settings (shown when the device is bound to a PC) flips that character between *Players* and *Auto-roll*; it POSTs to `/roll-pref` and `check_input.py` surfaces it to the DM as a `[[<Char> roll mode: …]]` directive that overrides the campaign default for that character.
- **"Move received" confirmation on the phone.** When the DM actually picks up the queued action (not just when it's staged), the phone shows a `✓ The DM has your move` toast and the status strip flips to *The DM is narrating…*, then back to *Your move*. Closes the loop the status strip was missing.

## [2.1.0] — 2026-06-04 — Reading text size, narration length, phone turn-flow

- **Reading text size control.** A `Text Size` stepper in Settings (`A−` / `A+`, click the % to reset) scales the reading column via a font-size multiplier — *font size, not page zoom* — so narration stays legible across the room from a Chromecast. Persists per-browser (`localStorage["dnd-text-scale"]`), applied anti-FOUC.
- **Narration length slider.** A `Narration` slider in Settings (250–2500 words) sets the target the DM aims for each turn. The value POSTs to `/narration-pref`; `check_input.py` prepends a `[[Narration length…]]` directive to queued player input so the DM honors it as a hard budget that turn (SKILL.md instructs it). Quick "keep turns short" control for time-pressed tables.
- **Phone turn-flow redesign.** The phone input view now shows a plain status strip — *Your move → Sending… → Sent to the DM* (accent banner) → back — so a player can always tell whether their turn is in. Staging is **one tap** (auto-ready: the action sends immediately). And device approval now defaults to trusting any LAN device (`DND_REQUIRE_APPROVAL=1` restores the approve/deny gate), removing the "Awaiting approval" button-clip and the approval-card overlap on a casual home network.
## [2.0.1] — 2026-06-05 — Doc fixes — README images, /dnd → /dm:dnd command refs

- **README image paths fixed.** Top-level README references to `display/icons/*.png` now correctly point at `skills/dnd/display/icons/*.png` (the restructure in v2.0.0 moved the assets but didn't update the README). The repo page on GitHub now renders the logo and feature-bullet icons correctly.
- **Stale `/dnd` command references swept to `/dm:dnd`.** `SKILL.md`, `SKILL-commands.md`, and `SKILL-scripts.md` had ~148 internal references to the retired `/dnd` slash command (e.g. `/dnd load`, `/dnd save`). The model loads these as its operating playbook; an instruction to "type `/dnd save`" no longer resolves under the plugin. All command references swept to `/dm:dnd`; path references like `~/.claude/dnd/` and `skills/dnd/` are unaffected.
- **`update_skill.py` plugin-mode heuristic narrowed.** The path-substring fallback for `CLAUDE_PLUGIN_ROOT`-less subprocesses now matches `.claude/plugins/` specifically, not any path containing `/plugins/`. Prevents a dev tree with a `plugins` directory in the path from false-positively reporting plugin-managed mode.

## [2.0.0] — 2026-06-04 — Plugin-only · Neural Initiative org · v1→v2 migration

- **Plugin-only distribution.** The standalone skill flavor (`~/.claude/skills/dnd`, `/dnd`) is retired; the skill ships solely as the Claude Code plugin **`dm@neural-initiative`**, invoked as **`/dm:dnd`**. Install with `/plugin marketplace add neuralinitiative/claude-dnd-skill` then `/plugin install dm@neural-initiative`. The v1.x standalone is frozen on a `legacy-1.x` branch (unmaintained). Maintaining both flavors doubled the test matrix and diverged silently; one packaging path is the supported path going forward.
- **Moved to the Neural Initiative org.** The repo lives at `github.com/neuralinitiative/claude-dnd-skill` and the marketplace is named `neural-initiative`. GitHub's permanent redirects keep existing `Bobby-Gray/*` URLs (clones, bookmarks, links) resolving indefinitely — no external breakage. The hosted version at [neuralinitiative.ai](https://neuralinitiative.ai) shares the same branding.
- **One-time v1→v2 migration helper.** `scripts/migrate_v1_to_v2.py` (+ [MIGRATING.md](MIGRATING.md)) detects a legacy standalone install, **carries over device approvals, the display auth token, and TLS certs** from the old in-skill `display/` dir into the update-safe runtime dir (so paired phones stay paired and HTTPS stays trusted), then backs up and retires the old skill so `/dnd` no longer shadows `/dm:dnd`. It is symlink-aware (won't clobber a Stow/dev link), idempotent, and supports `--dry-run` / `--yes` / `--keep-standalone`. **Campaign data is never moved** — it lives at the data root (`~/.claude/dnd` / `$DND_CAMPAIGN_ROOT`), shared identically by both versions, so characters/campaigns/history need no migration.

## [1.13.0] — 2026-06-04 — Editorial (Colossal) display redesign

- **Editorial display skin — a Colossal-inspired alternative to the classic Vellum look.** A new `Editorial` *style* sits beside Vellum (toggle in the display controls / `localStorage["dnd-skin"]`), with **Dark** and **Bold** (light) themes built on a Syne + Newsreader type system. Both the TV display and the phone companion are redesigned: a full-bleed reading column, a full-height party rail (d20 logo, HP, Factions/Quests), a right-edge launcher rail whose **Party Input** and **Settings** open sliding drawers, an icon **Phone Mode** button that jumps straight to the phone view, a phone **character-select** first screen with per-phone Theme/Style, themed character-sheet and SRD modals, and a DM-Help status toast. The classic look is untouched — the skin layers on via a `data-skin="editorial"` axis, so Vellum remains the default and is unchanged.
- **Writable runtime state moved out of the code directory.** Device approvals, TLS certs, auth tokens, the input queue, session state, and logs now live under a dedicated runtime dir (`paths.runtime_dir()` → `<data-root>/.runtime`, override with `DND_RUNTIME_DIR`) instead of inside `display/`. This means **device approvals and TLS certs survive `/plugin update`** (the plugin code dir is refreshed on update, which previously wiped them) and the display no longer depends on its install dir being writable. A shared `display/runtime_paths.py` resolves the location so every reader/writer agrees. Process-management files that are recreated on every launch (`app.pid`, `app.log`, `.cert-server.pid`, `.scheme`) intentionally stay in the code dir, so the `display stop` / `status` commands are unchanged.

- **Guided entry via `AskUserQuestion`.** A bare `/dnd` (no subcommand / ambiguous opener) now opens a structured picker — Load / New / Import / Manage character — instead of relying on the player to recall a subcommand. The campaign picker (`/dnd load` with no name) and the session-setup choice (display / LAN / autorun, at `/dnd load` and `/dnd new`) are likewise `AskUserQuestion` menus rather than typed y/n prompts. The menu is skipped whenever intent is already explicit (a subcommand or named campaign goes straight to its procedure), and free-form input (character concepts, themes, in-scene choices) stays prose. `AskUserQuestion` added to the skill's tool allowlist.

- **Packaged as a Claude Code plugin.** The skill can now be installed via the plugin system (`/plugin marketplace add ethan-piper/claude-dnd-skill` → `/plugin install dm@ethan-piper`) in addition to a manual clone. Structural changes: a `.claude-plugin/plugin.json` + `marketplace.json` manifest; skill files and all bundled assets (`scripts/`, `display/`, `data/`, `templates/`) nested under `skills/dnd/`; every in-skill code path resolved via `paths.py` (`skill_root()`) or `__file__` so it works whether installed as a plugin, a standalone skill, or a dev clone; SKILL-doc script references use `${CLAUDE_SKILL_DIR}`. `update_skill.py` / `/dnd update` is now install-mode aware — plugin installs are directed to `/plugin update`. **Campaign data is unaffected** (it lives under `~/.claude/dnd` / `$DND_CAMPAIGN_ROOT`, separate from the skill code). The plugin is named `dm` and provides the `dnd` skill, so the namespaced command is `/dm:dnd`; the manual standalone install keeps `/dnd`. Migration note in the README for existing standalone users.

- **License formalized as AGPL-3.0-or-later.** Added canonical `LICENSE` file with `Copyright (c) 2026 Neural Initiative LLC` and a `CONTRIBUTING.md` documenting the contribution licensing handshake. The README's prior informal "MIT" footer is replaced with a proper AGPL-3.0-or-later section linking to the LICENSE file. Self-hosting and modification remain explicitly welcome; AGPL protects against closed-source SaaS forks. Contributions made before this change remain available under their original (MIT-as-stated) terms in git history; new contributions are AGPL-3.0-or-later.

## [1.12.1] — 2026-05-31 — Audio-controls legibility fixes (table-test feedback on v1.12.0)

Four small fixes surfaced by table-side testing right after v1.12.0 landed. All in `display/templates/index.html`; no script or data changes.

### `.theme-label` was rendering at browser-default `<span>` size

The new "Dark / Light / Auto" value beside the Theme row was sitting at ~16px while every other label in `#audio-controls` was 7.5px Cinzel. The class was declared on the element but never had a CSS rule — fixed by folding `.theme-label` into the same combined declaration as `.speed-label` and `.narrate-label`. All three now share font, sizing, transitions, and the new pill-button styling described below.

### Click-to-cycle labels now look like buttons

The three stateful labels (Type Speed, Auto Narrate, Theme) all click to cycle through their states but rendered as plain text — the affordance wasn't readable. They now share a pill-button shape: subtle dark inset background, bronze 1px border, 4px corner radius, brighter font color. Active-state ("Auto Narrate: On", "Theme: Light/Auto") gets a stronger pill background so an engaged toggle reads as engaged even when nothing is hovered. Light-mode picks up the inverse — parchment-tinted pills with deep-ink text.

### Default audio-controls opacity bumped from 0.38 → 0.78

The cluster used to fade to barely-visible at rest, then to ~0.85 only when the cursor entered the cluster. Combined with the `rgba(*,0.75)` text alpha that gave ~0.28 effective alpha against the dark backdrop — basically illegible without hover. New defaults: cluster sits at 0.78 at rest, brightens to 1.0 on cluster hover. Individual labels start at brighter `rgba(220,185,100,0.85)` instead of `rgba(180,140,60,0.75)`.

### Per-row hover affordance

The cluster used to brighten as a single block on hover — every row lit up at once. Each `.audio-row` now has its own hover state: subtle background pill (`rgba(180,140,60,0.10)`), brighter label color (`rgba(245,215,130,1)`), and the speed/narrate/theme pills get a brighter border + background. The row currently under the cursor reads as the active click target. The SFX toggle-track (which doesn't have a label, so the pill rule doesn't apply) gets a brighter border on row-hover for the matching affordance.

### Dice-result text contrast bumped

`.dice-result` was `#c8a040` (muted bronze) with a faint `0.3` text-shadow and `0.18` border alpha — readable but easy to lose against dark scenery, called out at the table as "barely visible." Now `#f0d27a` (brighter gold) with a stronger amber glow `rgba(255,210,110,0.55)` plus a deep-ink drop shadow for depth, wrapped in a faint dark plate `rgba(40,28,8,0.35)` and a stronger border alpha `0.4`. Light-mode dice-result picks up a matching deep-ink-on-parchment treatment so the gold pill doesn't look like a dark hole on the cream page.

## [1.12.0] — 2026-05-31 — Light / dark / auto theme picker (vellum mode)

Adds a three-state theme picker to the display companion. Default behavior is unchanged — anyone who doesn't touch the picker continues to see the same dark, atmospheric display the skill has always shipped. The new options ride alongside it:

- **Dark** *(default)* — the existing ornate, atmospheric look.
- **Light** — parchment scroll on dark scenery. The body, vignette, and particle backdrop stay dark for contrast; `#text-content` becomes a cream "page" with deep-ink narration. Easier on the eyes during daylight sessions or on bright laptop screens; keeps the moody frame visible at the edges so the world doesn't lose its register.
- **Auto** — follows the operating system's `prefers-color-scheme`. Switches automatically when the OS toggles light/dark and tracks the change live (no reload).

### Selector placement

Lives as a fourth row in the existing top-right `#audio-controls` cluster, alongside Sound Effects / Type Speed / Auto Narrate. Same dim-at-rest, brighten-on-hover affordance as the other rows. Click to cycle through Dark → Light → Auto → Dark. The label shows the current selection, and the tooltip surfaces the effective resolved theme when in Auto mode (e.g. *"Auto (system: light)"*).

Placement was a deliberate choice — the existing right-side rail is the natural home for display preferences, since the skill doesn't have a free-floating chrome bar at the top of the screen the way some browser-app shells do. If you'd prefer the picker elsewhere (e.g. inside the character `#sidebar` once a campaign is loaded), the row is one stop on the audio-controls flexbox; trivial to move.

### Anti-FOUC

The applied theme persists per-browser in `localStorage["dnd-theme"]`. A small inline `<script>` in `<head>` reads that value and sets the `data-theme` attribute on `<html>` *before* the stylesheet parses — so any explicit Dark or Light choice avoids the dark→light or light→dark flash that would otherwise happen on every page load while the React-free Flask template hydrates. Auto mode leaves the attribute off and lets the `@media (prefers-color-scheme: light)` block in the stylesheet resolve naturally.

### Palette

The vellum palette is carried over directly from the Neural Initiative implementation that landed earlier today, so anyone running both surfaces sees a consistent reading experience:

- **Parchment page:** radial gradient from `rgba(255, 245, 220, 0.96)` at the top center fading to `rgba(238, 224, 188, 0.82)` at the edges.
- **Deep ink narration:** `rgb(40, 28, 14)` with a soft warm-light shadow `rgba(255, 250, 235, 0.6)` to give the type depth on the cream.
- **Bronze accents** for headings, NPC names, table borders, world clock, and UI chrome: `rgb(70, 50, 18)` / `rgb(110, 75, 20)` / `rgba(140, 95, 20, *)`.
- **Block-identity colors preserved** at darker shades: cool player blue `rgb(40, 70, 110)`, DM-tab purple `rgba(100, 55, 160, *)`, NPC bronze `rgba(95, 65, 18, 0.95)`, tutor moss `rgba(200, 230, 200, 0.45)` / `rgb(25, 70, 35)`, tutor-warning amber `rgba(245, 220, 175, 0.55)` / `rgb(110, 60, 15)`.
- **Action buttons** flip to dark plate + cream text in light mode so the affordance stays visually loud against the parchment.
- **Modal panels** (character sheet, SRD lookup) get the same parchment treatment with deep-ink text — the modal backdrop itself stays dark to dim the scenery behind it.

### What's covered

Every narration block type, the input panel, character tabs, all the v1.10.0+ TTS chrome (per-block bar, voice dropdown, auto-narrate label), v1.11.0 phone-mode + full-mode buttons, the dice-pending "Waiting on…" badge, both modal types and their content tables. ~430 lines of CSS overrides plus a small JS module driving the picker.

### What's not covered (yet)

- **Particle backdrop palette.** The `#particles` canvas (embers, dust, sparks, etc.) uses scene-tied accent colors that work fine against the dark scenery the light mode preserves around the parchment page. No palette swap needed for v1; revisit if specific scenes wash out at the edges.
- **Per-campaign override.** Currently global per-browser. The Neural Initiative side supports `localStorage["ni-theme-campaign:<id>"]` overrides; if you want the same in the skill, a `theme_override: light` flag in `state.md → ## Session Flags` is the natural shape and one follow-up away.

## [1.11.0] — 2026-05-31 — Phone-mode switcher + dice-pending badge XSS hardening

Follow-up release on top of the phone companion that landed in #38. Two items: a UX polish surfaced by table-side testing, and one defense-in-depth fix noted during the same session. Both client-side only; no server changes, no new endpoints, no new dependencies.

### Phone-mode switcher on the landing page

Typing `http://<lan-ip>:5001/?view=input&char=<CharacterName>` by hand was real friction the first time you handed a phone to a player. The base URL now carries a small "📱 Phone Mode" button on the right rail (above the existing audio-controls cluster) — click it to drop a character picker populated from the campaign's current player roster (read from the same `stats` payload that already feeds the sidebar and char tabs, so no new server endpoint). Pick a name and the browser navigates to the right `?view=input&char=` URL automatically.

Symmetric "👁 Full Display" button sits bottom-left in input mode for players who want to read recent narration off their own device — strips the `?view` / `?char` query params and jumps back to the base view in one click.

Pure client-side polish; the underlying phone-companion routing from #38 is unchanged. Dim at rest and bright on hover, matching the rest of the auxiliary control aesthetic.

### Dice-pending badge XSS hardening

The "Waiting on Piper, Mira…" badge that appears on the main display while DM-prescribed rolls are outstanding rendered the server's `dice_pending` snapshot via `innerHTML` to support the inline `<span class="dpb-label">` styling on the label string. Server-side validation on `POST /dice-request` strips `` ` ``, `$`, and `\` from the label and character names but leaves `<`, `>`, and `&` intact, which meant an approved phone could inject script into the DM's browser context via either field.

Practical attack surface is small — the X-DND-Token is the existing trust boundary across the codebase and anyone holding it already has substantial privileges — but the fix is one line per sink and worth applying as defense-in-depth. A shared `_escHtml()` helper now wraps both `e.label` and every `e.pending[]` member name before they reach the template literal. The new helper is also used by the mode-switcher dropdown above for symmetry, even though `textContent` would already escape there.

The server side of `POST /dice-request` is unchanged — string escaping at the boundary it pours into innerHTML is the right place for the fix and avoids breaking any caller that does want `<` or `>` literally in a label (rare, but possible).

## [1.10.0] — 2026-05-28 — Narrator TTS + i18n expansion to all 24 Gemini locales

Two additive features in this release, both opt-in. Existing campaigns are unaffected unless you choose to engage with the new capabilities.

### Narrator TTS via Gemini Flash TTS (optional)

Per-block speaker buttons on every `.dm-block` and `.npc-block` in the display companion, paired with a 9-voice dropdown (4 male: Charon, Enceladus, Fenrir, Umbriel; 5 female: Aoede, Gacrux, Kore, Vindemiatrix, Zephyr). Click the speaker to hear the block read aloud through your chosen narrator voice. Synthesis happens server-side via Google's Gemini Flash TTS through your own AI Studio API key — the full setup walkthrough at `docs/SKILL-tts.md` gets you running in about five minutes with a free Google account.

A per-browser **Auto Narrate** toggle in the top-right audio controls (saved to `localStorage`) auto-plays each new narration block on that browser only. Practical use: turn it on for the device casting to your TV and leave it off on player phones, so the audio plays from the main display while the phones stay quiet.

Voice selection is per-campaign — persisted to `state.md → ## Session Flags → tts_voice: <name>`. Switching voices mid-session updates the active marker across every visible block's dropdown simultaneously.

The feature is **off by default** and stays off until you configure an API key. With no key the speaker buttons don't render and the display behaves exactly as it does today. Three layered fail-silent gates back this up: no key → server returns 503 and the button stays inert; invalid voice → silent fallback to the default; upstream API error → button shows a brief diagnostic label for a few seconds, then resets, while text-only narration continues uninterrupted.

Browser-side playback uses AudioContext with manual Int16 → Float32 PCM conversion rather than HTMLAudioElement. iOS WebKit's per-call gesture gating on `<audio>` is hard to work around; AudioContext only needs `ctx.resume()` once per session inside a user gesture, so subsequent plays don't require a fresh gesture. The 2000-character input cap matches the point past which Gemini Flash TTS responses begin to degrade in latency and may truncate; longer DM responses are naturally chunked at block flush.

**Per-browser cost note.** Each player clicking the speaker on the same block triggers a separate Gemini call — there's no server-side caching by content hash. A 4-player table where everyone clicks every block is roughly 4× the per-block cost. Recommended mitigation: use Auto Narrate on the casting TV only, so the TV speaker carries the audio for the table. Setup doc also points at Google's billing-cap controls for users who want a hard ceiling.

Gemini Flash TTS auto-detects the input language from the text content, so the feature works transparently across all 24 supported locales — Chinese, Japanese, Spanish, Hindi, and the rest just synthesize correctly without any language code on the request.

### i18n expansion to all 24 Gemini-supported locales

PR #32's two-language SFX foundation (English + Chinese) is extended to all 24 locales Gemini Flash TTS supports: `ar`, `bn`, `de`, `en`, `es`, `fr`, `hi`, `id`, `it`, `ja`, `ko`, `mr`, `nl`, `pl`, `pt`, `ro`, `ru`, `ta`, `te`, `th`, `tr`, `uk`, `vi`, `zh`. Same dict-of-dicts language-pack structure as #32 — each language contributes trigger phrases per SFX category (impact, sword, arrow, shout, thud, magic, coins, door, low_hum, fire, breath). Latin scripts use word-boundary regex; unspaced scripts (CJK, Thai, Arabic) use literal substring matching.

The `_PRINTABLE` character allowlist and `_CHAR_NAME_RE` regex in `display/dnd-display-app.py` and `display/wrapper.py` widen to accept letters from every script in scope: Latin Extended A/B (`é ñ ö ć ş` etc.), Greek, Cyrillic, Hebrew, Arabic, Devanagari (Hindi/Marathi), Bengali, Tamil, Telugu, Thai, Vietnamese diacritics, Hiragana, Katakana, Hangul, and the existing CJK ranges. Player and NPC names in any of these scripts are now first-class without per-script special casing.

Default behavior is unchanged. The active language list stays `["en"]` (English-only) until explicitly overridden via the new `DND_SFX_LANGUAGES` environment variable (e.g. `export DND_SFX_LANGUAGES=en,zh,es`) or per-campaign via `state.md → ## Session Flags → sfx_languages: en,zh`. Both pre-existing English-only sessions and PR #32's `["en", "zh"]` configurations continue to work without modification.

Translation quality across the 22 new packs is best-effort starter content. Community PRs to refine any individual pack are warmly welcomed — the structure is designed for additive, language-by-language extension with zero code changes needed in the regex compiler.

### README — Other ways to play

The "Using a different LLM?" section additionally mentions `neuralinitiative.ai` as the hosted browser-based sibling project for users who'd rather skip a local install entirely. Matches the existing cross-reference tone — honest about the tradeoffs, optional, no marketing language. Existing `open-tabletop-gm` mention preserved as-is.

## [1.9.0] — 2026-05-22 — Community release: i18n + optional physical dice server (hardened)

Two community contributions landed in this release, both opt-in / additive — existing campaigns are unaffected unless you choose to engage with the new features.

### CJK input + multi-language SFX triggers (#32 — @canxer314)

Thanks to **@canxer314** for genuinely thoughtful i18n work. The `display/audio.py` SFX trigger map is refactored from inline regex patterns into a per-language data structure so future contributors can drop in Japanese, Korean, or any other language pack with minimal friction. A Chinese (`zh`) pack ships with this release. `_PRINTABLE` and `_CHAR_NAME_RE` in `display/dnd-display-app.py` and `display/wrapper.py` are widened to accept CJK Unicode ranges (U+4E00–U+9FFF, U+3400–U+4DBF, U+3000–U+303F, U+FF00–U+FFEF) without weakening the existing shell-injection filter. Backward-compatible by default — `_SFX_LANGUAGES = ['en']` keeps English-only behavior unless explicitly switched.

非常感谢您的贡献!

### Optional physical 3D dice server (#30 — @ethan-piper)

Thanks to **@ethan-piper** for a beautifully atmospheric piece of work. The new `dice-server/` directory adds an opt-in local Flask server + Three.js phone client that turns each player's phone into a tactile 3D dice tray. When the DM rolls for a PC, dice push to that player's phone — they shake or tap to cast, and the result returns to the campaign. NPC/DM rolls auto-resolve server-side. Real polyhedron geometry, brass PBR material with ACES tone mapping, synthesized clatter / thud / chime audio, and a "consecrate" ritual to anchor a player's tab to the table. Existing `dice.py` behavior is preserved when the server isn't running.

### Hardening applied on top of #30

A security-review skill pass on the dice-server PR flagged a handful of items worth tightening before release. None of these are structural changes — the contributor's core design and code are preserved:

- **`dice-server/server.py` default-bind to `127.0.0.1`.** Previously bound `0.0.0.0` unconditionally. Now binds localhost by default; pass `--lan` (or use the bundled launchd plist which sets the flag) to expose to phones on the LAN. Protects against accidental LAN exposure when developing or running the server briefly without thinking about the network.
- **Subresource Integrity (SRI) on the unpkg Three.js import.** `dice-server/dice.html` now uses a `<script type="importmap">` with `integrity` field pinning the SHA-384 hash of `three.module.js@0.160.0`. If unpkg ever serves modified content for that exact version, the browser refuses to execute it. SRI omitted on the Google Fonts CSS link because Google serves UA-varying content (no single hash works); inline comment documents the choice.
- **TTL sweep on the in-memory `rolls` dict.** Entries older than 1 hour are pruned on each `/roll`, `/submit`, `/spec`, and `/result` request. Cheap O(n) sweep; prevents long-running servers from accumulating roll records forever.
- **`scripts/dice.py` opt-in inverted.** Default is now `DND_DICE_PHYSICAL=0` (no server probe, zero added latency for users who didn't install the optional server). Opt-in two ways: set `DND_DICE_PHYSICAL=1`, or have `~/.dnd-dice/` exist (the `install-launchd.sh` script creates this marker automatically). Users who explicitly install the dice server get the physical-roll routing without doing anything else; users who never installed it pay no cost.
- **`SKILL-commands.md` health-probe conditional.** The `/dnd new` step 15 and `/dnd load` step 1 dice-server-check sub-step now short-circuits when neither `~/.dnd-dice/` exists nor `DND_DICE_PHYSICAL=1` is set — no `curl` probe at all for users who haven't installed the optional server.

---

## [1.8.0] — 2026-05-08 — D&D 5e 2024 (SRD 5.2) ruleset support

The skill now supports both **2014 (SRD 5.1)** and **2024 (SRD 5.2)**, declared per campaign on the `state.md` header line. **2014 stays the default** — nothing about an existing campaign changes unless the DM opts in. Routing happens at every `/dnd load` via `paths.campaign_ruleset()`. A backwards-compat migrator handles legacy campaigns with a one-time prompt and a timestamped backup before any write.

### What changed

**Per-campaign ruleset field**

`state.md` header gains `**Ruleset:** 2014` or `**Ruleset:** 2024`. `/dnd new` step 2 prompts at creation time. `/dnd load` reads the field every session and routes lookups, combat, and the procedural docs for character creation and level-up to the matching code path.

- **`paths.campaign_ruleset(name)`** reads the header, returns the declared value or the default (`2014`) for legacy campaigns. Exposed on the CLI as `paths.py campaign-ruleset <campaign>`.
- **`paths.srd_path(ruleset)`** resolves to `data/dnd5e_srd.json` (2014) or `data/dnd5e_srd_2024.json` (2024). Exposed as `paths.py srd-path [2014|2024]`.

**2024 SRD dataset (`data/dnd5e_srd_2024.json`)**

1,424 records pulled from CC-BY-4.0 sources — `5e-bits/5e-database` (`src/2024/en/`) and `foundryvtt/dnd5e` (`packs/_source/spells24/`, `actors24/`, `classfeatures24/`). Build with `python3 scripts/build_srd.py --ruleset 2024` (one-time, ~3 minutes).

- 341 native 2024 spells, 376 native 2024 monsters, 8 weapon mastery properties, 9 species, 24 subspecies, 17 origin / general / fighting-style feats, the 4 SRD 2024 backgrounds, plus equipment, magic items, conditions, and class features.
- Every record carries `_source` (e.g. `foundryvtt-2024`) and `_license` (`CC-BY-4.0`) so any single entry traces back to its origin.
- Foundry token resolvers in `_strip_html` and the actor-item path: `[[expr]]{label}` rolled-display, `@UUID[id]{label}`, `&Reference[id]{label}`, activity-aware lookup tokens, malformed `&Reference[id}` (curly-closer typo from upstream). Output is whole-dataset artifact-clean — bad-substring scan across 10 categories returns zero hits over 1,424 records.

**Weapon mastery in `combat.py`**

The 8 properties — Vex, Topple, Sap, Cleave, Graze, Nick, Push, Slow — wired into `combat.py attack --mastery <property>`. A Fighter applying Sap on a hit gets the disadvantage tracked on the target's next attack without anyone remembering the rule. Standalone `combat.py mastery <prop>` and `combat.py masteries` subcommands surface the canonical mechanical effect for narration.

**Backwards-compat migrator (`scripts/migrate_ruleset.py`)**

Legacy campaigns auto-prompt for migration on the next `/dnd load`.

- **`--check`** is strictly non-mutating. Exit codes: `0` = already migrated, `1` = needs migration, `2` = missing campaign.
- **`--ruleset 2014|2024 --yes`** stamps the field after backing up `state.md` to `state.md.backup-pre-ruleset-<timestamp>`. Idempotent — re-running on a migrated campaign is a clean no-op.
- The default for legacy campaigns is **2014** (they *were* 2014; silently flipping mid-arc would change character math under the players' feet). Explicit migration to 2024 via `--ruleset 2024 --yes` is a separate manual exercise.

**`lookup.py` ruleset-aware routing**

Accepts `--campaign <name>` (auto-routes via `paths.campaign_ruleset`) or `--ruleset 2014|2024` (explicit override). With no flag, defaults to 2014; emits a one-line stderr hint when the 2024 dataset is also present.

**Display companion ruleset badge**

Sidebar `#ruleset-badge` in the world-clock cluster — subdued amber for 2014, gold-accented for 2024. Server reads `paths.campaign_ruleset` on `/stats --set-campaign`; `push_stats.py` exposes `--ruleset` as an explicit override.

**Procedural updates**

- `/dnd new` step 2: ruleset selection question; build dataset on the spot if 2024 is chosen and missing.
- `/dnd load` step 2: `migrate_ruleset.py --check`; one-time prompt for legacy campaigns; renumbered subsequent steps.
- `/dnd character new` and `/dnd level up`: ruleset-aware branching for subclass-at-3, ASI source (race in 2014, background in 2024), and origin feats.

### Mechanic differences applied at the table

| Mechanic | 2014 | 2024 |
|---|---|---|
| Subclass timing | varies by class (1 / 2 / 3) | level 3 across the board |
| ASI source | race | background |
| Origin feat | n/a | granted at level 1 by background |
| Weapon mastery | n/a | 8 properties |
| Exhaustion | 6-level table with varied effects | 1 stack = -2 to all d20 rolls (cumulative); death at level 6 |

Combat resolution, dice, initiative, AC/HP derivation, XP tables, cantrip damage scaling, and rest recovery are identical between editions.

### Known limits

- **No mid-arc auto-rebuild.** Switching an in-progress campaign mid-arc is allowed by the migrator but doesn't recompute character builds.
- **Non-SRD 2024 supplemental content.** Wikidot has no 2024 surface yet; `build_supplemental.py --ruleset 2024` writes a stub file with documented `_meta`. Lookups for non-SRD content in a 2024 campaign degrade to the 2014 entry.
- **No mid-session ruleset switching from the display.** The badge surfaces the campaign's ruleset; there's no UI to flip it from the couch.

### Credit

- [`5e-bits/5e-database`](https://github.com/5e-bits/5e-database) — base SRD surface for both 2014 and 2024.
- [`foundryvtt/dnd5e`](https://github.com/foundryvtt/dnd5e) — extended 2024 packs (`spells24/`, `actors24/`, `classfeatures24/`, etc.). All foundry content is CC-BY-4.0 with provenance preserved per record.

---

## [1.7.5] — 2026-05-01 — Display robustness: send.py + tail persistence + arc pre-emption

Three long-standing reliability bugs land hard fixes in this release. Each one had been tripping live sessions repeatedly; the fixes are structural rather than patch-shaped, with regression tests so they don't come back.

### What changed

**send.py — body-bundling restored, integrity checks added**

The bug: when `--stat-*` flags were combined with a heredoc body (the documented pattern for bundling state changes with narration), `send.py` would dispatch the stat update but silently drop the narration text. Root cause was in the stdin-read decision: `_build_stats_payload(args)` was being treated as a "body-less" signal, so reading stdin was skipped even when a heredoc was attached.

- **Stdin decision rewritten.** Three categories are now distinguished cleanly:
  - Content flags (`--player`/`--npc`/`--dice`/`--tutor`/`--action`) → body required, stdin always read.
  - Truly body-less flags (`--inspiration-*`, `--xp-award`, `--milestone-*`) → stdin never read; safe in chained Bash blocks.
  - Stat flags (`--stat-*`, `--effect-*`) → body OPTIONAL; stdin is read when piped (heredoc), skipped when an interactive TTY (avoids blocking).

- **Pre-flight integrity checks.** Before posting, every payload runs through `_validate_payload(...)`: chunk payloads must have text or an award; multiple content tags (e.g. `--player` + `--npc`) are rejected; stats payloads must carry a list. Validation failures abort with `sys.exit(2)` and a clear stderr line including the offending payload — no more silent drops.

- **HTTP-level receipt verification.** The `_post(...)` helper now logs every send to a per-process `_SEND_LOG` and inspects the response status. Non-2xx responses surface the body excerpt to stderr. Connection refused (display offline) is the only "expected" silent failure.

- **Post-flight self-check.** After all sends, the script tallies failures from `_SEND_LOG`. Display-offline yields one quiet stderr line; any other partial failure yields a `PARTIAL FAILURE` summary and `sys.exit(3)` so the caller can react.

- **Optional `--verify` round-trip.** When set, after sending the script GETs the new `/health` endpoint to confirm the server is processing requests. Mismatches surface clearly. Use during dev/debug.

**dnd-display-app.py — non-destructive tail persistence + atomic writes**

The bug: `session_tail.json` was being silently wiped to `[]` between sessions, so `/dnd load` had no last-scene replay to play back. Replay had only worked once or twice across many sessions.

Root cause traced to a filtering+persistence chain in the tail buffer code. `_load_tail()` cleared the buffer first then re-appended campaign-filtered entries; if every entry filtered out (or the file was empty), the buffer ended up zeroed. The next `_persist_tail()` then wrote `[]` over the file. With dual-path resolution (campaign-side vs legacy fallback), the failure was hard to pin down.

- **`_load_tail` is now non-destructive.** Builds a candidate buffer first, only swaps it in if at least one entry survived filtering. Empty file → buffer left alone. All-filtered → buffer left alone. Corrupt JSON → buffer left alone, error logged.

- **`_persist_tail` skips on empty.** Refuses to overwrite an existing non-empty file with an empty buffer. Logs a stderr warning when it does so. Breaks the "wipe → persist → file lost" chain at the persistence end too.

- **Atomic writes.** `_persist_tail` writes to `<path>.tmp` then `os.replace(...)` — readers (the next `/dnd load`) can never see a partial or zero-byte state.

- **Legacy fallback path removed.** The skill-side `~/.claude/skills/dnd/display/session_tail.json` fallback is gone. Tails only ever land in the campaign-specific path. If `CAMP_FILE` is missing/empty, persist holds the buffer in memory and skips disk — no more silent bleed across campaigns.

- **`/health` endpoint added.** Returns `alive`, `tail_buffer` count, `tail_file_size`, `text_log` count, `campaign`, `clients`. No auth required (no PII). Used by `send.py --verify` and external monitors.

**`/dnd end` tail backstop + `verify_tail.sh` + `write_canonical_tail.py`**

Even with the in-process guards above, `/dnd end` now belt-and-suspenders the tail:

- Before killing the display, `verify_tail.sh <campaign>` checks the on-disk tail is healthy (>50 bytes, parses as a non-empty JSON list, entries have recognizable shape). Exit 0 = healthy; exit 1 = unsafe to rely on.
- If unhealthy, the DM writes a canonical replacement directly to disk via `write_canonical_tail.py` from session context — bypasses the display entirely so the file is good even if the server died unexpectedly. Atomic write, campaign-stamped, capped at 30 entries.
- Re-verification post-kill catches any final-write race.

**Beat 2b structural fix — pre-emption is a revision trigger**

The bug (campaign-level): when players act faster than the world, the `world_pressure` event for an outstanding beat plays out fully without the beat's `what_changes` consequence landing. Beats were going stale and steering notes were being rewritten manually.

Root cause: dynamic-arc beats were being generated with `what_changes` written event-shaped (something specific happens) when it should be consequence-shaped (something fundamentally different is true).

- **SKILL.md rule 8 added (Pre-emption is a revision trigger).** When `world_pressure` delivers but the consequence doesn't land, this triggers `/dnd arc revise` automatically at `/dnd end`. Three landing-path templates: **cost path** (the party paid for moving fast), **secondary consequence path** (the world responds to being pre-empted in a way the party didn't anticipate), **deferred path** (rewrite `world_pressure` toward the same consequence on a longer horizon).
- **`/dnd new` step 12 strengthened.** Arc-generation prompt now explicitly demands `what_changes` be consequence-shaped, with worked event-vs-consequence example.
- **`/dnd end` arc-check rewritten.** Now performs an explicit pre-emption check on each outstanding beat and auto-triggers `/dnd arc revise` when needed. The DM doesn't have to spot it.
- **`/dnd arc revise` enhanced.** Surfaces the three landing-path templates as a structured choice; before/after diff for the affected beat is shown for review.

### Tests

- New `tests/test_display_robustness.py` (18 tests) covers:
  - Stdin-read decision across all flag combinations including the bundled-stat regression
  - Payload validation (text-or-award required, multiple-content-tags rejected, stats-as-list)
  - Tail load: empty file / filtered-out / matching / corrupt JSON / missing CAMP_FILE
  - Tail persist: skip-on-empty-over-content / atomic / no-camp-no-write
- All 80 tests in the suite pass.

## [1.7.4] — 2026-05-01 — Stack-based milestone counter (backport from open-tabletop-gm v0.9.0)

A counted milestone counter for the rewards that don't fit Inspiration's binary shape — Bardic Inspiration dice, homebrew Hero Coins, Fate Tokens, alternate-system reward tokens, anything that *accumulates*. The existing Inspiration code is unchanged; the binary gold badge in the sidebar still works as before. This release adds a parallel, stack-based reward UI alongside it.

### What's new

- **`/dnd graph`-style send.py flags**:
  - `send.py --milestone-award NAME [--reason "..."] [--label "Bardic Inspiration"]`
  - `send.py --milestone-spend NAME [--label "..."]`

  Default label is `"Milestone"`. Use the system-specific term in `--label`.

- **Sidebar counter** — each player card renders one row per active milestone label with a gold count pill (`HERO COIN  3`). Empty labels don't render; a label is removed from the underlying `milestones` dict on decrement-to-zero.

- **Server-side mutation ops** `_milestone_inc` and `_milestone_dec` — same pattern as `_conditions_add` / `_slot_use`. Increments respect optional per-label `milestone_caps` (set to `{"Bardic Inspiration": 1}` for an effectively-binary reward). Decrements floor-clamp at 0.

- **Feed block** with gold-glow styling — `.milestone-block` rendered when an award fires. Persists to text_log + session tail; replays on browser reconnect.

### Test suite (62 total, up from 55)

`tests/test_milestone_counter.py` — 7 new tests covering increment, decrement, label removal at zero, cap enforcement, multi-label coexistence, decrement-below-zero clamping, and isolation from the existing binary `inspiration` boolean.

### Compatibility

- Existing campaigns: no change.
- Existing `inspiration_award` flow: untouched. The binary badge and gold-glow inspiration block continue to work.
- New `milestones` dict on player records: created on first award. No migration.

### Why this and not just inspiration?

D&D 5e's core Inspiration is binary — you have it or you don't. But many tables track *additional* reward currencies: Bardic Inspiration die counts, homebrew Hero Coins for great roleplay, Fate Tokens for cinematic moments, table-favorite "DM Coins" you can cash in for a reroll. The binary `inspiration` field can't represent these. Now you can:

```
$ python3 display/send.py --milestone-award "Aldric" --label "Bardic Inspiration" \
    --reason "rallied the crew at the harbor"
```

Sidebar shows `BARDIC INSPIRATION  1`. Award twice more → `3`. Spend one → `2`. Spend remaining → row disappears.

---

## [1.7.3] — 2026-05-01

Future-tense planning verbs land in the seed. The recall gap from earlier today's research is closed — the deterministic extractor now picks up GM session-prep prose like *"Vedra plans to file the nomination Friday"* or *"Mira intends to confront Aldric at dawn"*, not just past-tense narrative.

### What's new

- **Six new borderline verb entries**: `plans_to`, `intends_to`, `scheduled_to`, `aims_to`, `expected_to`, `targets`. All `lifetime: dispositional` (intentions can change). All medium-confidence by default — the GM should review before applying because the patterns are looser than past-tense SVO.
- **`V` wildcard in pattern templates** — represents a variable verb phrase (1–4 lowercase tokens) between a fixed modal phrase and an entity. Lets one template like `"X plans to V Y"` match `"plans to file"`, `"plans to meet"`, `"plans to ambush before dawn"` against the same regex. Implemented with `(?-i:...)` so the wildcard never accidentally consumes a capitalized entity prefix.

### Test suite (now 55 tests)

- Seven new `FutureTenseVerbTests` — V-wildcard captures the full canonical entity, doesn't consume capitalized names, all six new patterns match expected sentences, end-to-end extraction picks up `plans_to` / `intends_to` / `targets` edges from a synthetic session-log.

### Demo verification

A session 7 added to the Havenfall demo:

```
$ /dnd graph extract --deterministic --last-session-only
Captain Renna Voss --[plans_to]--> Mira Solveig    (medium)
  "Captain Renna Voss plans to ambush Mira Solveig at the docks."
Mira Solveig --[intends_to]--> Issaly Wreth        (medium)
  "Mira intends to flip Issaly Wreth before the harvest."
Brother Halvard --[targets]--> Mayor Aldric Brandt  (medium)
  "Brother Halvard targets Mayor Aldric Brandt in his sermons."
```

All three captured with verbatim source-anchors.

### What stays deferred

- **Phase 3 hybrid mode** (deterministic-first, LLM-fallback). Still on the design board, still unbuilt.

---

## [1.7.2] — 2026-05-01

Phase 2.5 — the two graph-feature follow-ups that needed real implementation rather than just design notes. Both ship behind the same opt-in pattern as Phase 2: existing campaigns and `graph.json` files keep working unchanged.

### What's new

- **`/dnd graph supersede-edge`** — mark an edge as superseded (hard retcon). Use when canon explicitly contradicts a prior extraction — a session log was corrected, or a relationship was misread. The wrong edge stays in the graph for audit trail; `scene-context` filters it out, but `subgraph` and `show` queries can still surface it for historical review. Optional `--by <correct-edge-id>` links to the replacement; `--reason "..."` records why. Distinct from `close-edge`: closing ends a real relationship cleanly; superseding says the original was wrong.

  ```
  /dnd graph supersede-edge --campaign havenfall --id e1 --by e9 \
    --reason "S6 retcon: Halvard framed the mayor; Theodora's intel was wrong"
  ```

- **Category-node edges**. State-verbs flagged `category_object_ok: true` in the verb seed (currently `possessed_by`, `worships`, `cleric_of`, `cursed_by`, `fears`, `flagged_offlimits`) now extract proposals where the verb's object is a categorical noun phrase (`a ghost`, `the silver veil`, `some old spirit`) rather than a uniquely-named entity. `extract-apply` auto-creates a node with `category_node: true` and `type: category`; `scene-context` renders it with an `(unnamed)` marker so the GM remembers the player canonically doesn't have a name yet.

  Example proposal:
  ```
  wraith --[possessed_by]--> Brother Halvard  (low) [X is category]
  Issaly Wreth --[worships]--> silver veil    (medium) [Y is category]
  ```

  Categorical proposals start at lower confidence than entity-only matches — the captured noun ("ghost") is genuinely ambiguous in a way that named entities aren't.

### Schema additions

- **`superseded_by: <edge-id> | true`** — optional field on edges. Set by `supersede-edge`. `_edge_active_at()` excludes any edge with this field truthy.
- **`supersede_reason: "..."`** — optional companion field with the human explanation.
- **`closed_anchor`** (recap from v1.7.1) — verbatim phrase justifying a closure. Now also rendered in `scene-context` output when present.
- **Node `category_node: true`** — flag on auto-created category nodes. Display layer renders these with an "(unnamed)" suffix.

### Tests

Suite is now **48 tests in ~2s**:
- 12 verb-table sanity tests
- 25 deterministic-extractor tests
- 11 end-to-end CLI tests, including:
  - `supersede-edge` marks the edge AND filters it out of `scene-context` at later sessions
  - `supersede-edge` without `--by` (retcon with no replacement) records `superseded_by: true`
  - `extract --deterministic` on a possession scene (`"Aldric is possessed by a ghost"`) produces a categorical proposal
  - `extract-apply` auto-creates a `cat_*` node with `category_node: true`

### Bug fixes

- **Category target slot detection**: the deterministic extractor was picking the emit's `to` slot as the categorical target, but verbs like `possessed_by` emit `{from: Y, to: X}` — Y is the grammatical object, not the emit's `to`. The category slot is now resolved from the LAST `X/Y/Z` placeholder in the template (the verb's grammatical object in SVO), not from emit metadata.
- **Section-header pluralization**. `## categorys` → `## categories`. One more node type and we'd have noticed eventually; cosmetic but caught it in the demo.

### What stays deferred

- Future-tense planning verbs (`plans_to`, `intends_to`, `scheduled_to`) — still need the DM session-prep corpus pass.
- Hybrid Phase 3 mode (deterministic-first, Haiku-fallback on unmatched sentences).

---

## [1.7.1] — 2026-05-01

The Phase 2 deterministic extractor lands one day after the Phase 1 release. We had been planning to ship this as a separate v1.8 minor; on second look it's better framed as completing what v1.7.0 promised. The Haiku-backed `extract` is still there and unchanged — `--deterministic` is a new opt-in flag on the same subcommand.

This release also adds a test suite (45 tests across three files) so future changes to the graph extractor don't regress what was just shipped.

### What's new

- **`extract --deterministic`** — pattern-matches sentences against `data/graph/verb_table_seed.yaml` instead of calling Haiku. Output format is identical to the LLM extractor; `extract-apply` accepts proposals from either path. Zero LLM cost. Estimated recall ~50%, precision ~95% on clean SVO and SVO-with-prep relationships. The Haiku path stays the default until your campaigns have run with the deterministic extractor for a while.
- **`extract --last-session-only`** — narrow extraction to the most recent `## Session N` block of `session-log.md` (skip the archive). Useful for end-of-session sweeps.
- **`extract-apply --review`** — interactive proposal-by-proposal walkthrough with `y / n / q` prompts. Each prompt shows the verbatim source anchor and confidence. Mutually exclusive with `--pick`. Quitting (`q`) honours edges already accepted but stops processing further proposals.
- **`close-edge --anchor "..."`** — record the verbatim phrase that justifies the closure as a new `closed_anchor` field on the edge. The original since-anchor in `source.anchor` is preserved; closure history is now also auditable. Optional and backwards-compatible.

### Schema additions to the verb-table seed

- **`lifetime: event | state | dispositional`** annotated on every inclusion + borderline entry (119 entries). Distinguishes immutable past occurrences (`killed`, `told`) from ongoing relationships (`serves`, `married_to`) from drift-over-time stances (`fears`, `committed_to`). The deterministic extractor uses this to scope future state-end logic.
- **`category_object_ok: true`** flag on state-verbs whose object is often a category rather than a unique entity (`possessed_by a ghost`, `worships the gods`). Currently informational; consumed by Phase 2.5 work on category-node edges.

### Test suite

- **`tests/test_verb_table.py`** (12 tests) — sanity checks on the seed: YAML parses, every entry has `lifetime`, lifetime values valid, spot-checks against known event/state/dispositional verbs, `category_object_ok` only on state/dispositional.
- **`tests/test_deterministic_extract.py`** (25 tests) — entity recognizer (npcs.md / npcs-full.md / world.md), sentence splitter, pattern regex builder, alias index (first-word / surname / middle-subsequence aliases, stop-word rejection, ambiguity skipping, canonical precedence), session-number resolution, end-to-end on a synthetic campaign, dedup, `--last-session-only` behavior, empty-campaign handling.
- **`tests/test_campaign_graph.py`** (8 tests) — end-to-end against the actual CLI: `add-node` / `add-edge` / `close-edge` (with and without `--anchor`), `scene-context` filtering by `--at-session` (closed edges hidden), uninitialized-graph notice, `extract --deterministic` write, `extract-apply --pick` subset application.

Run from repo root: `python3 -m unittest discover tests -v`

### Bug fixes

- **`build_alias_index` now generates surname / middle-subsequence aliases** in addition to the first-word alias, so a session-log saying "Aldric Brandt" or just "Brandt" still resolves to the canonical "Mayor Aldric Brandt". The previous first-word-only behaviour caused the deterministic extractor to miss most of a real campaign's relationships.
- **Stop words never become aliases**. `### The Council` in `world.md` was previously generating a `"The"` alias that case-insensitively matched every "the" in any sentence, producing edges like `Drave Cors --[met]--> the`. Articles, prepositions, and conjunctions are now excluded from alias promotion.
- **Verb-table loader handles both repo layouts.** `data/graph/verb_table_seed.yaml` (public repo) and `data/verb_table_seed.yaml` (live skill) are both checked, so the deterministic extractor works in either location without manual configuration.

### What stays deferred

- **Phase 2.5 schema additions** — `closed: {session, anchor}` as an object (the lightweight `closed_anchor` field is shipped instead), category-node edges via `category_object_ok` consumption, hard-retcon `superseded_by` field. Documented in `docs/research/graph/phase-2-3-plan.md`.
- **Hybrid mode (Phase 3)** — pattern-first then Haiku-fallback on unmatched sentences. Defer until Phase 2 has soaked.
- **Future-tense planning verbs** — `plans_to`, `intends_to`, `scheduled_to`. Need a separate corpus pass on DM session-prep documents (Reddit narrative is past-tense).

---

## [1.7.0] — 2026-05-01

Earlier than expected. The v1.6.0 notes said *"the implementation lands in v1.7+"*, which framed the work as somewhere on the horizon. After looking at it again the morning after — the implementation has been running in our own live campaigns for weeks, the A/B study showed clear and bounded behaviour, and the risk profile of holding it back was higher than the risk of shipping it. So it ships now.

The campaign relationship graph is no longer a research preview. It is a feature.

### What's new

- **`scripts/campaign_graph.py`** — full extractor + query engine in the canonical skill. Subcommands: `init`, `add-node`, `add-edge`, `close-edge`, `list`, `show`, `subgraph`, `scene-context`, `extract`, `extract-apply`. Local-only, time-stamped (`since_session` / `until_session`), with verbatim source-anchors on every edge.
- **Auto-pull at `/dnd load`.** The scene-context query runs as part of the load flow, before the recap, so Claude has the active subgraph in scope before it speaks. If the graph isn't initialized yet, the load flow offers an auto-init with a backup-first prompt — see below.
- **Sweep at `/dnd save`.** The save flow scans the session for relationship shifts that weren't recorded live and presents them to the DM as a numbered list (`y / pick / skip`) before writing.
- **`/dnd graph` command suite** documented end-to-end in `SKILL-commands.md`. The README command table covers the most common subcommands.

### Backwards-compatible auto-init

Existing campaigns don't have a graph. The `/dnd load` flow now handles this gracefully:

> *"This campaign doesn't have a relationship graph yet. I can initialize one now — it improves long-session continuity recall when `npcs-full.md` falls out of context. As a safety precaution, I'll back up the campaign first to `~/.claude/dnd/campaigns/<name>.backup-YYYYMMDD-HHMMSS/`. Proceed? [y/n]"*

`y` runs a `cp -R` snapshot before anything touches the campaign, then proposes seed nodes and edges from the existing markdown for DM approval. `n` continues without the graph for that session and doesn't re-prompt. No silent extraction, no auto-write — the same review-then-apply discipline that's been in the sandbox since Phase 1.

### What stays in `docs/research/graph/`

- **`phase-2-3-plan.md`** — design for the deterministic verb-table extractor (Phase 2) and hybrid path (Phase 3). Phase 2 ships when the three schema additions (`closed`, `lifetime`, `category_object_ok`) and `--review` interactive apply land.
- **`ab-experiment-findings.md`**, **`verb-gap-categories.md`** — the research record. Useful background reading for anyone running into similar continuity-gap issues; not required to use the feature.
- **`discussion-post-draft.md`** — community write-up draft for GitHub Discussions.

### What's next

- Phase 2 deterministic extractor (zero LLM cost, ports cleanly to the LLM-agnostic [open-tabletop-gm](https://github.com/Bobby-Gray/open-tabletop-gm) fork).
- Schema additions: `closed` field on state edges, `lifetime` column on verbs, `category_object_ok` flag for state-verbs whose object is often categorical.
- A separate corpus pass on DM session-prep documents to capture future-tense planning verbs (`plans_to`, `intends_to`).

Thanks again to [@ethros19](https://github.com/ethros19) (Ethan Piper) for the issue #7 thread and the months of long-campaign feedback that made the case for shipping this rather than holding it.

---

## [1.6.0] — 2026-04-30

Today's update is a quiet but meaningful one, and it lands on a problem that's been with us — and the broader LLM-RPG community — since very early on. [@ethros19](https://github.com/ethros19) (Ethan Piper) first surfaced it in [issue #7](https://github.com/Bobby-Gray/claude-dnd-skill/issues/7) back in v1.4 days, and has been the most consistent voice keeping us honest about the long-context failure modes ever since. The shape of the problem: after enough sessions, the DM voice will sometimes treat a known character as a fresh contact — *"go see the chandler, tell him I sent you"* — when the player and that character were introduced sessions ago. The relationship facts are always in the canon. They've just fallen out of scope after compaction.

We've shipped a few different methodologies for this over the last several months. The compaction-drift fix in 1.4 (re-read the source, never trust the compacted impression). The Live State Flags block (cover, faction stances, dispositions in compact key-value form). The targeted-Read directives in `SKILL.md`. Each one held the line at the scale of the campaigns we were running at the time.

Campaigns kept getting longer. The continuity surface kept getting wider. This release is the next scale of that same arc — a structural relationship graph alongside the markdown files, with verbatim source-anchors on every edge, designed to surface relationship facts cheaply when the full files are no longer in context. We think it scales much higher than the previous tools, and the research below explains why.

This release publishes the research, the design, and the tooling. It does **not** flip the implementation on yet — that lands in v1.7+. For now, what's new is documentation, data, and the version-tracking infrastructure that's been overdue for a while.

Existing campaigns are unaffected. Nothing here changes how you play today.

### Versioning is now tracked

- New `VERSION` file at the repo root. New `CHANGELOG.md` (this file) with the full history reconstructed from past releases.
- `/dnd update --check` now shows local vs. remote version side by side, so it's obvious at a glance whether you've fallen behind.

If you've ever wondered which copy of the skill you have, that's resolved.

### Campaign-graph research preview

A typed-edge relationship graph that runs alongside `npcs.md`, `state.md`, and `session-log.md`. Every edge carries a verbatim source-anchor pointing back to the line in canon that asserts it — so every claim the graph makes is auditable.

You can read everything we've put together so far under `docs/research/graph/`:

- **A/B experiment findings.** A controlled replay study (60 generations across three prompt-shape variations) measuring whether the graph reduces continuity errors. The honest answer turned out to be more interesting than the original hypothesis.
- **Verb-table gap audit.** A 17-category taxonomy of NPC-relationship verbs and which ones the seed table is missing. Eight new edge types are promoted in this release, including `possessed_by`, `in_love_with`, `swore_oath_to`, and `sworn_enemy_of`.
- **Phase 2 / 3 plan.** The design for the deterministic extractor that ships next. Six friction findings from Phase 1 live-trial, three new schema fields specified.

The supporting tooling is here too:

- `data/graph/verb_table_seed.yaml` v0.5 — saturated seed of ~50 inclusion verbs from 1,014 observations across 7 live campaigns, 4 published adventures, and 280 Reddit narrative posts.
- `scripts/graph/experiment_replay.py` — the A/B harness, configurable for your own gap-prone moment.
- `scripts/graph/external_corpus_collect.py` + `external_corpus_extract.py` — the Reddit collector and Haiku verb-frequency extractor we used to build the seed.

If any of that is interesting to you, the docs are written to be read on their own.

### Bug fixes

- **Spell slots no longer 500 on long rest.** Display-side payloads using the legacy `{remaining, max}` slot schema were tripping a `KeyError` during slot restoration. The server now accepts both shapes silently. This affects you only if you've seen "spell slots not restoring" after a long rest; you don't need to do anything to apply the fix.

### What's next

- v1.7 will land the deterministic graph extractor and the `/dnd graph` commands in the live skill — opt-in per campaign, behind a clear "experimental" marker.
- The schema changes (`closed` field on state edges, `lifetime` column on verbs, `category_object_ok` flag) need to ship before promotion to canonical.
- A separate corpus pass on DM session-prep documents is on the list to capture future-tense planning verbs (`plans_to`, `intends_to`) — those don't show up in past-tense narrative posts, which is the gap we already know about.

Thanks for sticking with this through Phase 1. The research turned out richer than we expected, and the deterministic path is now clear.

---

## [1.5.0] — 2026-04-30

This was the last pre-versioning release. PR #14 closed out the long tail of display companion polish that had been accumulating, plus the two skill-management commands that needed to land before version tracking could ship.

### What's new

- **`/dnd update`** — pull skill changes from `origin/main`. Refuses on a dirty tree, fast-forward only, so it never silently merges divergent history.
- **`/dnd path`** — view or relocate campaign storage via `DND_CAMPAIGN_ROOT`. Useful if you keep your campaigns in iCloud, on a network drive, or anywhere other than the default location.
- **Inspiration awards now render the reason inside the block.** Previously the reason landed only in the sidebar badge and you had to look twice to see why someone got it.

### Bug fixes

- **`send.py` no longer hangs on chained-bash invocations.** Body-less flags (like `--inspiration-award` or `--xp-award`) were waiting on stdin that never came. Detection skips the read entirely now.
- **Display tail replay** correctly resumes on session reload.
- **Heredoc gotcha warning** added to the send-batching docs after one too many missed `${VAR}` expansions.

---

## [1.4.0] — 2026-04-20

A big one. Every campaign now has a committed three-act narrative shape generated at `/dnd new`, and the DM is aware of it during play.

The arc isn't a script. Each of its six beats is defined by what *changes* in the story when it lands, not by what specifically happens. That gives Claude flexibility on how each beat arrives while committing to the fact that it must arrive. We've been running this on live campaigns for over a week and it's the difference between "the session was fun" and "the session was fun *and* it moved the story forward."

### What's new

- **Dynamic arc system.** Auto-generated from the world's threat, factions, and Three Truths. Six beats: 1a/1b (setup), 2a/2b (confrontation), 3a/3b (resolution). The arc commits to a thematic resolution. The shape bends; it doesn't break.
- **`/dnd arc advance <beat>`** — mark a beat complete at session end. Updates `outstanding_beats` automatically.
- **`/dnd arc revise`** — when a player choice significantly redirects the story, the arc adjusts outstanding beats to fit the new direction without retconning what already happened.
- **`/dnd arc new`** — once all six beats land, generate a new arc from the consequences of the first. Same world, new story question.
- **Arc-aware DM steering.** Claude reads `## Campaign Arc` at every session load. World pressure for the next beat lands as a visible event before the beat itself. No beats delivered cold.

### Bug fixes

- **Compaction drift (#7).** When the conversation context compacts, Claude's impression of faction states and NPC dispositions becomes lossy. The DM rules now require re-reading the source — the smallest section that covers the claim — before any recap or status statement. A new `## Live State Flags` block in `state.md` makes that re-read cheap: cover, faction stances, and dispositions live there in compact key-value form.

---

## [1.3.0] — 2026-04-16

Two quality-of-life improvements that, in combination, made tracking spell durations and non-SRD content much less painful.

### What's new

- **Timed effect tracking.** `tracker.py` now tracks effect start/end with rounds/minutes/hours/indefinite durations. Auto-expiry warnings fire when a Bless wears off mid-combat or a Hex's hour is up. Concentration syncs automatically.
- **`send.py --stat-*` flags.** HP, spell slots, conditions, concentration, inventory, effect-start, effect-end — all bundle with the narration send in a single call. No more separate `push_stats.py` round-trip just to update one stat.
- **Supplemental SRD dataset.** `dnd5e_supplemental.json` covers non-SRD spells and features (Xanathar's, Tasha's, subclass features). `build_supplemental.py` fetches descriptions from dnd5e.wikidot.com for any character feature not in the bundled SRD.

---

## [1.2.0] — 2026-04-15

The bundled SRD release. Spell and feature lookup is now offline, instant, and clickable from the character sheet.

### What's new

- **Bundled `dnd5e_srd.json`** — 1,453 records: spells, equipment, magic items, conditions, monsters, class features. No download required at runtime.
- **`/dnd data sync`** — rebuilds the dataset from upstream sources (5e-bits + FoundryVTT) only when their SHAs change. Idempotent; safe to run anytime.
- **Clickable spell and feature lookups** in the character sheet modal — tap any name to view the full description. Wikidot fallback link for anything not in the local data.

---

## [1.1.0] — 2026-04-14

This is the release that changed how the table actually plays. Before this, players told the DM what they wanted to do and the DM typed it. After this, players use their phones.

It also paved the way for autorun, which made running a campaign with a partner who isn't quite a DM possible.

### What's new

- **Player input form on the companion UI.** Players submit actions from a phone or tablet on the local network. The action lands in `.input_queue` until the DM presses Enter (or autorun fires), so Claude's context stays under DM control.
- **Autorun mode** (`/dnd autorun on`). Claude drives the turn loop without DM input. Player submissions are sanitized, character-validated, and content-checked before they enter context. A pie countdown on the display shows the next auto-fire window.
- **LAN mode.** The companion serves over your local network. Every device in the room — TV, tablet, phones — sees the same display.
- **TLS / HTTPS.** Self-signed cert generation included. Required for full browser-feature support over LAN, particularly player input from devices other than localhost.

---

## [1.0.0] — 2026-04-13

The point at which the skill felt complete enough to call it a stable foundation. The releases before this were still actively reshaping fundamentals; from this point forward, additions are additions, not rewrites.

### What's new

- **Spell slots in the sidebar.** Pip-graph rendering by level. Live updates from `--stat-slot-use` / `--stat-slot-restore` so the table sees a slot consumed the moment it's cast.
- **Faction panel in the sidebar.** Auto-refreshed faction stances and descriptions.
- **Relationship rendering** in NPC entries — the *Knows / Owes / Fears* block surfaces visibly.
- **Haiku description lookup** for SRD entries. Formats results without round-tripping through Sonnet, so the lookup feels instant.
- **DM Help button (◈).** One-shot contextual hint at the press of a button. Distinct from tutor mode, which is ongoing.

---

## [0.9.0] — 2026-04-12

Quietly the most consequential release before 1.0. We split the system prompt and added targeted-search tooling — and the result was that context bloat stopped being the limiting factor on long campaigns. Everything that came after this depends on the architecture decisions made here.

### What's new

- **`SKILL.md` split** into three files: core rules (always loaded into the system prompt), `SKILL-scripts.md` (script syntax), and `SKILL-commands.md` (command procedures). The latter two load once at session start. Core stays small; reference material is on demand.
- **Context optimization architecture.** Campaign data is tiered: the NPC index is always loaded, full entries pull only when a character becomes relevant, quest hooks and worldbuilding stay in cold storage until called for.
- **`campaign_search.py`** — targeted keyword search across campaign files. Replaces full-file Reads for most recap and status questions, which means more sessions fit in context before compaction matters.
- **Per-viewer DM Hints toggle** on the display companion.
- **Narrative structure standards** + faction and node templates surfaced in `world.md` (Adventure Nodes as situations, not plots).

---

## [0.8.0] — 2026-04-11

A small release with two distinct beneficiaries: brand-new players and experienced players who like to look things up.

### What's new

- **Tutor mode** (`/dnd tutor on`) — automatic hint blocks after every scene, decision point, and roll. Optional, session-scoped, ideal for players new to D&D.
- **SRD data tools.** Initial `data_pull.py` and `lookup.py` for spell and feature reference.
- **Character sheet modal fixes.** Clickable cards open a full sheet (attacks, features, inventory) cleanly on phones and tablets over LAN.

---

## [0.7.0] — 2026-04-11

The companion's visual identity took shape in this release. If you've seen the demo GIF, this is the version it's recorded against.

### What's new

- **LAN mode** for the display companion. Serve over your local network; cast to a TV, mirror to a second monitor, or open on a tablet at the table.
- **Browser-side sound effects.** 12 SFX types synthesized via numpy and played through Web Audio API. Works on any device with the tab open, including phones over LAN.
- **Dynamic sky canvas.** Sun arc, moon, twinkling stars, cloud density — all rendered in real time from world time data. Transitions with time of day and weather.
- **17 scene types**, auto-detected from narration keywords (tavern, dungeon, ocean, crypt, arcane, glacier, and a dozen more). Each one has its own particle effect set.
- **Model routing policy** formalized: Script / Haiku / Sonnet / Opus tiers per task class.
- **Clickable character sheet modal** on sidebar player cards.

---

## [0.1.0] — 2026-04-09

The first commit. Persistent campaigns, full 5e mechanics, atmospheric DM tone, real dice via Python `random`, the twelve applied DM standards, world-generation wizard, and the character creation and import flow.

The shape of the skill was already there. Everything since has been about deepening it.

---

## Versioning policy

- **PATCH** (1.6.x) — bug fixes, doc updates, corpus additions. No behavior change.
- **MINOR** (1.x.0) — new commands, new scripts, new opt-in features. Existing workflows continue to work without modification.
- **MAJOR** (x.0.0) — breaking change to campaign data format, command rename/removal, or workflow that requires migration.

Tag releases with `git tag v<version>` and update both `VERSION` and `CHANGELOG.md` in the same commit. Tags follow `vX.Y.Z` format.
