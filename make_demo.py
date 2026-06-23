"""
make_demo.py — generates demo.gif for the claude-dnd-skill README.

Simulates the cinematic display companion: sky canvas, typewriter narration,
scene crossfade, character sidebar, sound-effects toggle, and combat tracker.

Usage:
    python3 make_demo.py          # writes demo.gif next to this script
"""

import math, os, random
from PIL import Image, ImageDraw, ImageFont

# ── Canvas ────────────────────────────────────────────────────────────────────
W, H = 1200, 720
FPS  = 18          # target frame rate (ms per frame = 1000/FPS)
DELAY = 1000 // FPS

random.seed(42)

# ── Colour palette ────────────────────────────────────────────────────────────
BG_TAVERN  = [(18, 10, 4),  (45, 25, 8),  (30, 14, 4)]
BG_OCEAN   = [(4, 12, 22),  (8, 30, 52),  (12, 22, 38)]
TEXT_GOLD  = (212, 196, 160)
TEXT_DIM   = (140, 120, 85)
TEXT_WHITE = (230, 220, 200)
BORDER_GOLD = (180, 140, 60, 90)
SIDEBAR_BG  = (10, 8, 5, 200)
TOGGLE_OFF  = (60, 50, 35)
TOGGLE_ON   = (160, 110, 30)

# ── Font helpers ──────────────────────────────────────────────────────────────

def _font(size, bold=False):
    candidates = [
        "/System/Library/Fonts/Supplemental/Georgia.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()

F_BODY  = _font(17)
F_SMALL = _font(13)
F_TITLE = _font(14, bold=True)
F_SCENE = _font(11)
F_DICE  = _font(15)

# ── Sky renderer ──────────────────────────────────────────────────────────────

def draw_sky(draw: ImageDraw.ImageDraw, img: Image.Image,
             t: float, phase: str = "morning", weather: str = "calm"):
    """
    t      : animation time in seconds
    phase  : dawn | morning | midday | evening | night
    weather: calm | overcast | rainy | stormy
    """
    sky_h = int(H * 0.42)

    # Sky gradient
    if phase == "night":
        top_col = (4, 6, 18); bot_col = (8, 12, 28)
    elif phase == "dawn":
        top_col = (25, 15, 45); bot_col = (90, 40, 20)
    elif phase == "evening":
        top_col = (35, 18, 8);  bot_col = (80, 35, 10)
    elif phase == "midday":
        top_col = (20, 55, 110); bot_col = (40, 80, 130)
    else:  # morning
        top_col = (18, 40, 80);  bot_col = (35, 65, 105)

    # Overcast / stormy tint
    if weather in ("overcast", "rainy", "stormy"):
        strength = {"overcast": 0.45, "rainy": 0.65, "stormy": 0.85}[weather]
        top_col = tuple(int(c * (1 - strength) + 20 * strength) for c in top_col)
        bot_col = tuple(int(c * (1 - strength) + 28 * strength) for c in bot_col)

    for y in range(sky_h):
        frac = y / sky_h
        r = int(top_col[0] + (bot_col[0] - top_col[0]) * frac)
        g = int(top_col[1] + (bot_col[1] - top_col[1]) * frac)
        b = int(top_col[2] + (bot_col[2] - top_col[2]) * frac)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    # Stars (night / dawn)
    if phase in ("night", "dawn"):
        rng = random.Random(7)
        star_alpha = 0.9 if phase == "night" else 0.3
        for _ in range(120):
            sx = rng.randint(0, W)
            sy = rng.randint(0, sky_h - 20)
            twinkle = 0.5 + 0.5 * math.sin(t * 2.3 + sx * 0.07 + sy * 0.05)
            a = int(star_alpha * twinkle * 200)
            draw.ellipse([sx-1, sy-1, sx+1, sy+1], fill=(220, 215, 255, a))

    # Sun / Moon
    sun_positions = {
        "dawn":    (int(W * 0.15), int(sky_h * 0.78)),
        "morning": (int(W * 0.25), int(sky_h * 0.35)),
        "midday":  (int(W * 0.50), int(sky_h * 0.18)),
        "evening": (int(W * 0.75), int(sky_h * 0.35)),
        "night":   None,
    }
    sp = sun_positions.get(phase)

    if phase == "night":
        # Crescent moon
        mx, my = int(W * 0.72), int(sky_h * 0.22)
        mr = 22
        # Glow
        for gr in range(mr + 18, mr, -2):
            ga = int(12 * (1 - (gr - mr) / 18))
            draw.ellipse([mx-gr, my-gr, mx+gr, my+gr], fill=(200, 210, 240, ga))
        draw.ellipse([mx-mr, my-mr, mx+mr, my+mr], fill=(230, 235, 255))
        # Shadow disk (crescent)
        offset = int(mr * 0.55)
        draw.ellipse([mx-mr+offset, my-mr, mx+mr+offset, my+mr],
                     fill=top_col + (0,) if len(top_col) == 3 else top_col)
        # Make shadow match sky color
        draw.ellipse([mx-mr+offset, my-mr, mx+mr+offset, my+mr], fill=top_col)

    elif sp and weather not in ("stormy",):
        sx, sy = sp
        sun_r = 26
        # Glow
        glow_col = (255, 230, 120) if phase not in ("dawn", "evening") else (255, 140, 60)
        for gr in range(sun_r + 28, sun_r, -2):
            ga = int(18 * (1 - (gr - sun_r) / 28))
            draw.ellipse([sx-gr, sy-gr, sx+gr, sy+gr],
                         fill=glow_col + (ga,))
        draw.ellipse([sx-sun_r, sy-sun_r, sx+sun_r, sy+sun_r],
                     fill=(255, 245, 180))

    # Clouds
    cloud_count = {"calm": 2, "clear": 1, "overcast": 5, "rainy": 5, "stormy": 5}
    cloud_alpha = {"calm": 90, "clear": 60, "overcast": 170, "rainy": 190, "stormy": 210}
    n_clouds = cloud_count.get(weather, 2)
    c_alpha  = cloud_alpha.get(weather, 90)
    cloud_col = (200, 200, 210) if weather == "calm" else (110, 110, 120)

    rng2 = random.Random(3)
    for i in range(5):
        base_x = rng2.randint(0, W)
        base_y = rng2.randint(int(sky_h * 0.08), int(sky_h * 0.55))
        w_scale = rng2.uniform(1.2, 2.2)
        if i >= n_clouds:
            continue
        cx = (base_x + int(t * 14 * (0.7 + i * 0.15))) % (W + 200) - 100
        for j in range(8):
            ox = rng2.randint(-55, 55)
            oy = rng2.randint(-18, 18)
            r  = rng2.randint(22, 42)
            draw.ellipse([cx+ox-r, base_y+oy-r, cx+ox+r, base_y+oy+r],
                         fill=cloud_col + (c_alpha,))

    # Bottom fade to transparent
    for y in range(int(sky_h * 0.55), sky_h):
        frac = (y - sky_h * 0.55) / (sky_h * 0.45)
        a = int(frac * 255)
        draw.line([(0, y), (W, y)], fill=(0, 0, 0, a))


# ── Background gradient ───────────────────────────────────────────────────────

def draw_bg(draw, palette, alpha=1.0):
    stops = palette
    section = H // (len(stops) - 1)
    for i in range(len(stops) - 1):
        c0, c1 = stops[i], stops[i + 1]
        for y in range(section):
            gy = i * section + y
            if gy >= H: break
            frac = y / section
            r = int(c0[0] + (c1[0] - c0[0]) * frac)
            g = int(c0[1] + (c1[1] - c0[1]) * frac)
            b = int(c0[2] + (c1[2] - c0[2]) * frac)
            a = int(alpha * 255)
            draw.line([(0, gy), (W, gy)], fill=(r, g, b, a))


# ── Vignette ──────────────────────────────────────────────────────────────────

def draw_vignette(draw):
    for step in range(60):
        frac = step / 60
        r_ell = int(W * 0.3 + W * 0.7 * frac)
        a = int(frac ** 1.6 * 180)
        cx, cy = W // 2, int(H * 0.45)
        draw.ellipse([cx - r_ell, cy - r_ell, cx + r_ell, cy - int(r_ell * 0.65)],
                     fill=(0, 0, 0, 0), outline=None)
    # simple corner darkening
    for y in range(H):
        fy = abs(y - H / 2) / (H / 2)
        a  = int(fy ** 2.2 * 140)
        draw.line([(0, y), (W, y)], fill=(0, 0, 0, a))


# ── Gold border frame ─────────────────────────────────────────────────────────

def draw_frame(draw):
    pad = 16
    draw.rectangle([pad, pad, W - pad, H - pad],
                   outline=(180, 140, 60, 80), width=1)
    # Corner accents
    sz = 20
    corners = [(pad, pad), (W - pad, pad), (pad, H - pad), (W - pad, H - pad)]
    segs = [
        [(0, 0), (sz, 0)], [(0, 0), (0, sz)],
        [(-sz, 0), (0, 0)], [(0, 0), (0, sz)],
        [(0, 0), (sz, 0)], [(0, -sz), (0, 0)],
        [(-sz, 0), (0, 0)], [(0, -sz), (0, 0)],
    ]
    for ci, (cx, cy) in enumerate(corners):
        for dx0, dy0, dx1, dy1 in [
            (0, 0, sz if cx == pad else -sz, 0),
            (0, 0, 0, sz if cy == pad else -sz),
        ]:
            draw.line([(cx + dx0, cy + dy0), (cx + dx1, cy + dy1)],
                      fill=(180, 140, 60, 160), width=1)


# ── Sidebar ───────────────────────────────────────────────────────────────────

def draw_sidebar(draw, hp_pct=1.0, xp_pct=0.72, combat=None, t=0):
    sx, sy, sw = 28, 60, 210
    # Background
    draw.rectangle([sx, sy, sx + sw, H - 60], fill=(8, 6, 4, 195))
    draw.rectangle([sx, sy, sx + sw, H - 60], outline=(180, 140, 60, 40), width=1)

    y = sy + 14

    # Character header
    draw.text((sx + 10, y), "ALDRIC", font=F_TITLE, fill=(200, 170, 80))
    y += 18
    draw.text((sx + 10, y), "Human · Fighter · Lv 3", font=F_SCENE, fill=TEXT_DIM)
    y += 20

    # HP bar
    draw.text((sx + 10, y), "HP", font=F_SCENE, fill=TEXT_DIM)
    hp_val = int(22 * hp_pct)
    draw.text((sx + sw - 10, y), f"{hp_val}/22", font=F_SCENE, fill=TEXT_GOLD,
              anchor="ra")
    y += 14
    bw = sw - 20
    draw.rectangle([sx+10, y, sx+10+bw, y+6], fill=(30, 22, 12), outline=(60,50,30))
    bar_col = (
        (60, 170, 60) if hp_pct > 0.6 else
        (200, 160, 20) if hp_pct > 0.3 else
        (190, 45, 35)
    )
    draw.rectangle([sx+10, y, sx+10+int(bw*hp_pct), y+6], fill=bar_col)
    y += 14

    # XP bar
    draw.text((sx + 10, y), "XP", font=F_SCENE, fill=TEXT_DIM)
    draw.text((sx + sw - 10, y), "900/1000", font=F_SCENE, fill=TEXT_DIM, anchor="ra")
    y += 14
    draw.rectangle([sx+10, y, sx+10+bw, y+4], fill=(30, 22, 12), outline=(60,50,30))
    draw.rectangle([sx+10, y, sx+10+int(bw*xp_pct), y+4], fill=(100, 75, 20))
    y += 14

    # Stats
    draw.line([(sx+10, y+4), (sx+sw-10, y+4)], fill=(60,50,30))
    y += 12
    stats = [("AC", "17"), ("INIT", "+1"), ("SPD", "30")]
    for i, (label, val) in enumerate(stats):
        col = sx + 10 + i * 66
        draw.text((col, y), label, font=F_SCENE, fill=TEXT_DIM)
        draw.text((col, y + 13), val, font=F_TITLE, fill=TEXT_GOLD)
    y += 34

    # Ability scores
    draw.line([(sx+10, y), (sx+sw-10, y)], fill=(60,50,30))
    y += 8
    abilities = [("STR","16","+3"),("DEX","12","+1"),("CON","15","+2"),
                 ("INT","10","+0"),("WIS","11","+0"),("CHA","13","+1")]
    for i, (ab, score, mod) in enumerate(abilities):
        col = sx + 10 + (i % 3) * 66
        row = y + (i // 3) * 32
        draw.text((col, row), ab, font=F_SCENE, fill=TEXT_DIM)
        draw.text((col, row+12), mod, font=F_TITLE, fill=TEXT_GOLD)
    y += 72

    # Combat tracker
    if combat:
        draw.line([(sx+10, y), (sx+sw-10, y)], fill=(60,50,30))
        y += 8
        draw.text((sx + 10, y), f"— COMBAT — Round {combat['round']}", font=F_SCENE,
                  fill=(190, 140, 40))
        y += 16
        for name in combat["order"]:
            is_cur = name == combat["current"]
            prefix = "▶ " if is_cur else "  "
            col = TEXT_GOLD if is_cur else TEXT_DIM
            draw.text((sx + 10, y), prefix + name, font=F_SCENE, fill=col)
            y += 14


# ── Scene indicator ───────────────────────────────────────────────────────────

def draw_scene_label(draw, label, alpha=1.0):
    a = int(alpha * 140)
    draw.text((W - 28, H - 32), label.upper(), font=F_SCENE,
              fill=(180, 140, 60, a), anchor="ra")


# ── Sound effects toggle ──────────────────────────────────────────────────────

def draw_sfx_toggle(draw, on=False):
    tx = W - 175
    ty = 28
    draw.text((tx, ty), "Sound Effects", font=F_SCENE, fill=TEXT_DIM)
    track_x, track_y = tx + 100, ty
    track_w, track_h = 36, 16
    track_col = TOGGLE_ON if on else TOGGLE_OFF
    draw.rounded_rectangle([track_x, track_y, track_x+track_w, track_y+track_h],
                            radius=8, fill=track_col, outline=(100,80,40))
    thumb_x = track_x + (track_w - 14) if on else track_x + 2
    draw.ellipse([thumb_x, track_y+2, thumb_x+12, track_y+14], fill=(220,200,140))


# ── Typewriter text ───────────────────────────────────────────────────────────

NARRATION = (
    "The harbour stinks of brine and old rope. "
    "Gulls wheel overhead as the tide pushes in, grey and cold. "
    "A dockworker shoulders past without a glance — "
    "then the door to the chandlery swings open and steel catches the morning light."
)

DICE_LINE = "Aldric — Perception: d20 + 1 = 17  →  Sharp"


def wrap_text(text, font, max_w):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if font.getlength(test) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


# ── Frame builder ─────────────────────────────────────────────────────────────

def make_frame(t: float, char_count: int, scene: str, sky_phase: str,
               weather: str, hp_pct: float, sfx_on: bool,
               combat=None, scene_alpha=1.0, dice_chars=0) -> Image.Image:

    base = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    bg   = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bgd  = ImageDraw.Draw(bg)

    palette = BG_OCEAN if scene == "ocean" else BG_TAVERN
    draw_bg(bgd, palette)
    base = Image.alpha_composite(base, bg)

    # Sky canvas
    sky_img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    skyd = ImageDraw.Draw(sky_img)
    draw_sky(skyd, sky_img, t, sky_phase, weather)
    base = Image.alpha_composite(base, sky_img)

    # Particles (simplified dots)
    parts = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pd    = ImageDraw.Draw(parts)
    rng   = random.Random(int(t * FPS))
    if scene == "tavern":
        for _ in range(18):
            px = rng.randint(250, W - 30)
            py = rng.randint(int(H * 0.35), H - 60)
            r  = rng.randint(1, 3)
            a  = rng.randint(60, 160)
            pd.ellipse([px-r, py-r, px+r, py+r], fill=(255, 160, 60, a))
    elif scene == "ocean":
        for _ in range(14):
            px = rng.randint(250, W - 30)
            py = rng.randint(int(H * 0.55), H - 60)
            rw = rng.randint(8, 20)
            pd.ellipse([px-rw, py-4, px+rw, py+4], outline=(72, 136, 192, 55), width=1)
    base = Image.alpha_composite(base, parts)

    # Vignette
    vig = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    vd  = ImageDraw.Draw(vig)
    draw_vignette(vd)
    base = Image.alpha_composite(base, vig)

    # UI layer
    ui = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d  = ImageDraw.Draw(ui)

    draw_frame(d)
    draw_sidebar(d, hp_pct=hp_pct, combat=combat, t=t)
    draw_sfx_toggle(d, on=sfx_on)
    draw_scene_label(d, scene, alpha=scene_alpha)

    # Narration text
    text_x, text_y = 260, 90
    text_w = W - text_x - 40
    full   = NARRATION[:char_count]
    lines  = wrap_text(full, F_BODY, text_w)

    draw_y = text_y
    for li, line in enumerate(lines):
        # cursor blink on last line
        is_last = li == len(lines) - 1
        txt = line
        if is_last and char_count < len(NARRATION):
            if int(t * 2) % 2 == 0:
                txt += "▎"
        d.text((text_x, draw_y), txt, font=F_BODY, fill=TEXT_GOLD)
        draw_y += 36

    # Dice line
    if dice_chars > 0:
        dice_text = DICE_LINE[:dice_chars]
        draw_y += 14
        d.text((text_x, draw_y), dice_text, font=F_DICE, fill=(200, 170, 60))

    base = Image.alpha_composite(base, ui)
    return base.convert("P", palette=Image.ADAPTIVE, colors=256)


# ── Sequence definition ───────────────────────────────────────────────────────

def build_sequence():
    """
    Returns list of (frame_image, delay_ms) tuples.

    Sequence:
      0.0 – 2.0s  : night sky, empty screen fades in
      2.0 – 6.0s  : narration typewriters in (ocean scene)
      6.0 – 8.5s  : scene crossfades tavern, sky→morning
      8.5 – 11.0s : narration continues, hp ticks down
      11.0–13.0s  : combat tracker appears, turn advances
      13.0–15.5s  : dice line types in, SFX toggle flips on
      15.5–18.0s  : hold, sky transitions to evening
    """
    frames = []

    TOTAL    = 18.0
    n_frames = int(TOTAL * FPS)

    narr_len = len(NARRATION)

    combat_data = {
        "order": ["Aldric", "Skeleton", "Pirate"],
        "current": "Aldric",
        "round": 2,
    }

    for fi in range(n_frames):
        t = fi / FPS

        # Scene
        if t < 6.0:
            scene = "ocean"
        else:
            scene = "tavern"

        # Scene crossfade alpha (label fades in/out during transition)
        scene_alpha = 1.0
        if 5.8 < t < 6.8:
            scene_alpha = 0.3 + 0.7 * abs(t - 6.3) / 0.5

        # Sky phase
        if t < 6.0:
            sky_phase = "night"
            weather   = "calm"
        elif t < 8.5:
            frac = (t - 6.0) / 2.5
            sky_phase = "dawn" if frac < 0.5 else "morning"
            weather   = "calm"
        elif t < 15.5:
            sky_phase = "morning"
            weather   = "overcast" if t > 11.0 else "calm"
        else:
            sky_phase = "evening"
            weather   = "calm"

        # Narration characters
        if t < 2.0:
            chars = 0
        elif t < 11.0:
            progress = (t - 2.0) / 9.0
            chars = min(int(progress * narr_len * 1.4), narr_len)
        else:
            chars = narr_len

        # HP
        if t < 9.5:
            hp_pct = 1.0
        elif t < 11.0:
            hp_pct = max(0.36, 1.0 - (t - 9.5) / 1.5 * 0.64)
        else:
            hp_pct = 0.36

        # Combat
        combat = None
        if t >= 11.0:
            cdata = dict(combat_data)
            if t > 12.5:
                cdata = dict(combat_data, current="Skeleton")
            combat = cdata

        # Dice line
        if t >= 13.0:
            dice_progress = min(1.0, (t - 13.0) / 1.5)
            dice_chars = int(dice_progress * len(DICE_LINE))
        else:
            dice_chars = 0

        # SFX toggle
        sfx_on = t >= 13.5

        frame = make_frame(
            t=t,
            char_count=chars,
            scene=scene,
            sky_phase=sky_phase,
            weather=weather,
            hp_pct=hp_pct,
            sfx_on=sfx_on,
            combat=combat,
            scene_alpha=scene_alpha,
            dice_chars=dice_chars,
        )

        # Hold the last ~2s longer
        delay = DELAY if t < 16.5 else DELAY * 3

        frames.append((frame, delay))
        if fi % FPS == 0:
            print(f"  rendered {t:.1f}s / {TOTAL:.1f}s")

    return frames


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    out = os.path.join(os.path.dirname(__file__), "demo.gif")
    print("Building demo.gif …")
    frames = build_sequence()
    print(f"Saving {len(frames)} frames to {out} …")

    imgs    = [f for f, _ in frames]
    delays  = [d for _, d in frames]

    imgs[0].save(
        out,
        save_all=True,
        append_images=imgs[1:],
        duration=delays,
        loop=0,
        optimize=False,
    )
    size_mb = os.path.getsize(out) / 1e6
    print(f"Done — {out}  ({size_mb:.1f} MB)")
