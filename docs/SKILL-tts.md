# Narrator TTS setup (optional)

The display companion can read narrator and NPC blocks aloud via Google's Gemini Flash TTS. It's optional, off by default, and the rest of the skill works fine without it. This guide gets you a working setup in ~5 minutes using a free Google account.

If you skip this guide, the display still renders text exactly as it does today — no audio, no warnings, no behavior change.

## What you get

- A speaker button at the bottom of every narrator and NPC block. Click to hear that block read aloud.
- A 9-voice dropdown (4 male, 5 female) sitting next to the speaker button. Change voice mid-session.
- An optional **Auto Narrate** toggle in the top-right audio controls. When on, every new narrator/NPC block auto-plays in **your browser only** — perfect for a TV or main-display cast device while player phones stay quiet.
- Multi-language support — Gemini auto-detects the language from the text, so a campaign played in Spanish, Japanese, Hindi, or any of the [24 supported languages](https://ai.google.dev/gemini-api/docs/speech-generation) just works.

## What it costs

Gemini Flash TTS bills per character. Typical narration block is ~600 characters; current pricing is ~$0.001 per block. A 3-hour session with ~30 narration blocks costs roughly 3¢. **The free tier handles casual use** — you only need to enable billing if you hit rate-limit errors or if you're a new AI Studio account (Google requires prepaid billing for new accounts as of 2026).

## Setup — three steps, ~5 minutes

### 1. Get a Gemini API key from Google AI Studio

This is the easiest path. No `gcloud`, no Cloud Console, no service accounts.

1. Visit **https://aistudio.google.com/apikey** and sign in with your Google / Gmail account. Accept the terms on first visit.
2. Click **Create API key**. If it asks which project to use, accept the default — Google will create one.
3. Copy the key. It looks like `AIza...` and is roughly 39 characters.

### 2. Save the key to your local config

```bash
mkdir -p ~/.config/claude-dnd && chmod 700 ~/.config/claude-dnd

# Paste the key when prompted, press Return, then Ctrl-D:
cat > ~/.config/claude-dnd/tts.key

chmod 600 ~/.config/claude-dnd/tts.key
```

The skill reads from this path automatically. If you'd rather use an environment variable, export `DND_TTS_KEY` (or `GEMINI_API_KEY`) instead and skip the key file — env vars take precedence.

### 3. Verify

From the skill base directory:

```bash
python3 display/tts.py --test
```

You should see:

```
API key source: file:/Users/you/.config/claude-dnd/tts.key
Model: gemini-2.5-flash-preview-tts
Voice: Enceladus
Text:  'Hello, narrator voice test. The torchlit hall awaits.'
Calling Gemini Flash TTS…
  OK — received 76800 bytes of L16 PCM (24 kHz mono).
```

To also hear it (macOS only):

```bash
python3 display/tts.py --test --speak
```

If verification fails, check the **Troubleshooting** table at the bottom.

## Using it during a session

Once the key is configured and the display companion is running:

- A small speaker icon appears at the bottom-right of every narrator (`.dm-block`) and NPC (`.npc-block`) block. Click to play. Click again to stop.
- A **Voices** dropdown next to it lets you switch narrator voice. The selection persists per-campaign in `state.md → ## Session Flags → tts_voice: <name>`.
- The **Auto Narrate** row in the top-right audio controls is per-browser — toggle it on for your TV cast, off on your player phones. Setting is saved in `localStorage`.

Player input blocks, dice-roll blocks, and tutor/help blocks intentionally **don't** get a speaker button — they're metadata, not narrative voice. The 2000-character cap on the synthesis endpoint is the upper bound; longer narration blocks are truncated server-side.

## Voice catalog

Curated 9-voice subset from Gemini's 30-voice catalog, scoped for narrative DM voices.

| Group | Voice | Notes |
|---|---|---|
| Male | Charon | Low, gravelly — heavies, villains |
| Male | **Enceladus** *(default)* | Deep, measured — classic narrator |
| Male | Fenrir | Rough, growling — feral characters |
| Male | Umbriel | Soft, reflective — sages and elders |
| Female | Aoede | Clear, bright — heroic / informative |
| Female | Gacrux | Mature, warm — innkeepers, mentors |
| Female | Kore | Youthful, energetic |
| Female | Vindemiatrix | Crisp, formal — nobles, scholars |
| Female | Zephyr | Light, airy — fey, sprites |

To expand the dropdown to Gemini's full 30 voices, edit `_TTS_VOICES_MALE` / `_TTS_VOICES_FEMALE` in `display/templates/index.html` and add the new names to `VALID_VOICES` in `display/tts.py`. The full catalog is documented at [Google's speech-generation guide](https://ai.google.dev/gemini-api/docs/speech-generation).

## Per-browser cost surfacing

Synthesized clips are cached on disk, keyed by a hash of the voice, the model and the text, so replaying a block — or a second player clicking the same block in the same voice — plays the saved clip and makes no call to Gemini. The cache lives at `~/.cache/claude-dnd/tts/` (move it with `DND_TTS_CACHE_DIR`), is bounded at 64 MB by default (`DND_TTS_CACHE_MB`; least-recently-played clips are evicted first), and `DND_TTS_CACHE_MB=0` turns it off. `tts.py --test` always calls the API, so it still checks your key. A block in a different voice is a different clip and is billed once. Two further mitigations if cost is a concern:

1. Use **Auto Narrate on the casting TV only** — players hear the audio from the TV speaker and don't click their own phones.
2. Set a daily spend cap on your Google billing project at [console.cloud.google.com/billing](https://console.cloud.google.com/billing).

## Multi-language sessions

Gemini Flash TTS auto-detects the input language from the text content. To play a Spanish-language campaign, just narrate in Spanish — the same `/tts` endpoint comes back synthesized correctly. The voice catalog stays identical across languages.

To also wire up SFX trigger packs (sword-clash sounds, magic shimmer, etc.) for non-English narration, set the active SFX languages either via environment:

```bash
export DND_SFX_LANGUAGES=en,es     # English first, then Spanish
```

…or per-campaign via `state.md → ## Session Flags`:

```
sfx_languages: en,zh
```

The skill currently ships SFX packs for all 24 Gemini-supported languages (`ar`, `bn`, `de`, `en`, `es`, `fr`, `hi`, `id`, `it`, `ja`, `ko`, `mr`, `nl`, `pl`, `pt`, `ro`, `ru`, `ta`, `te`, `th`, `tr`, `uk`, `vi`, `zh`). Community PRs to extend any pack are welcome.

## Path B — `gcloud` restricted key (advanced, optional)

If you already use the `gcloud` CLI and would rather mint a key scoped to *only* the TTS API — so a leak can't reach Cloud Storage, BigQuery, or other Google services on the same project — use this path:

```bash
PROJ=my-dnd-tts                  # any globally-unique project id
BILLING=YOUR-BILLING-ID          # gcloud billing accounts list

gcloud projects create "$PROJ"
gcloud billing projects link "$PROJ" --billing-account="$BILLING"
gcloud services enable generativelanguage.googleapis.com --project="$PROJ"

mkdir -p ~/.config/claude-dnd && chmod 700 ~/.config/claude-dnd
gcloud alpha services api-keys create \
  --project="$PROJ" \
  --display-name="claude-dnd-tts" \
  --api-target=service=generativelanguage.googleapis.com \
  --format='value(response.keyString)' \
  > ~/.config/claude-dnd/tts.key
chmod 600 ~/.config/claude-dnd/tts.key
```

The `--api-target` restriction means a leaked key can only call `generativelanguage.googleapis.com` on this specific project. Disable / rotate without affecting any other surface.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `python3 display/tts.py --test` says "API key: unset" | No env var **and** no key file — save your key to `~/.config/claude-dnd/tts.key`. |
| Speaker button shows "TTS 401" | API key invalid or disabled — re-mint at https://aistudio.google.com/apikey. |
| Speaker button shows "TTS 403" | Key not authorized for `generativelanguage.googleapis.com` (Path B keys), or billing not configured on a new AI Studio account. |
| Speaker button shows "TTS 429" | Free-tier rate limit, or new AI Studio account without billing — enable billing at https://aistudio.google.com or set a prepaid balance. |
| Speaker button shows "TTS 503" | Server reports TTS not configured — re-verify the key file and restart the display. |
| Audio doesn't play but no error label | Check device volume; on iOS Safari, click the speaker once to grant the AudioContext gesture, then auto-narrate will work for the rest of the session. |
| 1-3 second delay before audio starts | Normal — Gemini Flash TTS synthesis latency. Type Speed `Fast` paired with auto-narrate gives the tightest pairing of text and voice. |

## How to disable

Delete the key file:

```bash
rm ~/.config/claude-dnd/tts.key
```

The speaker buttons disappear from the display on next page load. Nothing else changes.
