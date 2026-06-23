"""
demo_server.py — self-contained demo loop for screen-recording demo.gif.

Serves the real index.html and feeds it a scripted sequence via SSE,
using the exact same wire format as app.py. Loops forever.

Usage:
    python3 demo_server.py
    open http://localhost:5002

Stop with Ctrl-C.
"""

import json
import os
import queue
import threading
import time

from flask import Flask, Response, render_template

# ── Path setup ────────────────────────────────────────────────────────────────
_DISPLAY_DIR = os.path.join(os.path.dirname(__file__), "skills", "dnd", "display")

app = Flask(__name__, template_folder=os.path.join(_DISPLAY_DIR, "templates"))

# ── SSE broadcast ─────────────────────────────────────────────────────────────
_clients: list = []
_clients_lock = threading.Lock()


def _broadcast(payload: dict):
    data = "data: " + json.dumps(payload) + "\n\n"
    with _clients_lock:
        dead = []
        for q in _clients:
            try:
                q.put_nowait(data)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _clients.remove(q)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/stream")
def stream():
    q = queue.Queue(maxsize=120)
    with _clients_lock:
        _clients.append(q)

    def gen():
        try:
            yield "data: {}\n\n"
            while True:
                try:
                    yield q.get(timeout=20)
                except queue.Empty:
                    yield ": keepalive\n\n"
        except GeneratorExit:
            pass
        finally:
            with _clients_lock:
                if q in _clients:
                    _clients.remove(q)

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})


@app.route("/ping")
def ping():
    return "ok"


# ── Broadcast helpers (match app.py SSE wire format exactly) ──────────────────

def push_world_time(date, day_name, time_of_day, season, weather):
    _broadcast({"stats": {"world_time": {
        "date": date, "day_name": day_name,
        "time": time_of_day, "season": season, "weather": weather,
    }}})


def push_players(players: list, replace=False):
    payload = {"players": players}
    if replace:
        payload["replace_players"] = True
    _broadcast({"stats": payload})


def push_hp(name: str, current: int, maximum: int):
    _broadcast({"stats": {"players": [
        {"name": name, "hp": {"current": current, "max": maximum}}
    ]}})


def push_turn_order(order: list, current: str, round_num: int):
    _broadcast({"stats": {"turn_order": {
        "order": order, "current": current, "round": round_num,
    }}})


def push_turn_current(name: str, round_num: int = None):
    upd = {"current": name}
    if round_num is not None:
        upd["round"] = round_num
    _broadcast({"stats": {"turn_order": upd}})


def push_turn_clear():
    _broadcast({"stats": {"turn_order": None}})


def push_text(text: str):
    _broadcast({"text": text})


def push_player(name: str, text: str):
    _broadcast({"text": text, "player": name})


def push_dice(text: str):
    _broadcast({"text": text, "dice": True})


def push_sfx(name: str):
    _broadcast({"sfx": name})


def push_clear():
    _broadcast({"clear": True})


# ── Demo sequence ─────────────────────────────────────────────────────────────

ALDRIC = {
    "name": "Aldric",
    "race": "Human", "class": "Fighter", "level": 3, "background": "Soldier",
    "hp": {"current": 28, "max": 28, "temp": 0},
    "xp": {"current": 900, "next": 1000},
    "ac": 17, "initiative": "+1", "speed": 30,
    "hit_dice": {"remaining": 3, "max": 3, "die": "d10"},
    "second_wind": True,
    "ability_scores": {
        "str": {"score": 16, "mod": "+3"}, "dex": {"score": 12, "mod": "+1"},
        "con": {"score": 15, "mod": "+2"}, "int": {"score": 10, "mod": "+0"},
        "wis": {"score": 11, "mod": "+0"}, "cha": {"score": 13, "mod": "+1"},
    },
}


def run_sequence():
    """One full pass through the demo — call in a loop."""

    # ── Reset ─────────────────────────────────────────────────────────────────
    push_clear()
    time.sleep(0.6)

    # ── Night harbour ─────────────────────────────────────────────────────────
    push_world_time("3 Ashveil 1312 AR", "Moonday", "night", "Long Hollow", "clear")
    push_players([ALDRIC], replace=True)
    time.sleep(1.0)

    push_text("The harbour stinks of brine and old rope.")
    time.sleep(3.2)
    push_text("Gulls wheel overhead as the tide pushes in, grey and cold under a thin crescent moon.")
    time.sleep(3.8)
    push_sfx("breath")
    push_text("A lantern sways above the chandlery door, casting amber arcs across the wet dock planks.")
    time.sleep(3.5)
    push_text("Somewhere in the dark, rigging creaks — then goes still.")
    time.sleep(3.0)

    # ── Dawn breaks ───────────────────────────────────────────────────────────
    push_world_time("3 Ashveil 1312 AR", "Moonday", "dawn", "Long Hollow", "calm")
    time.sleep(2.2)
    push_text("Hours pass. The eastern sky bleeds orange and the fishermen begin to stir.")
    time.sleep(3.5)
    push_world_time("3 Ashveil 1312 AR", "Moonday", "morning", "Long Hollow", "calm")
    push_sfx("door")
    push_text("Steel catches the first light as a dockworker shoulders past — then the chandlery door swings open.")
    time.sleep(3.8)

    # ── Tavern ────────────────────────────────────────────────────────────────
    push_text("The Rusty Anchor smells of tallow and yesterday's ale. A fire pops in the hearth.")
    time.sleep(3.5)
    push_sfx("fire")
    push_text("The barkeep eyes Aldric from across the bar — says nothing, sets a cup down hard.")
    time.sleep(3.2)

    # ── Player action + roll ──────────────────────────────────────────────────
    push_player("Aldric", 'Aldric slides a coin onto the bar. "Who owns the warehouse on pier four?"')
    time.sleep(3.2)
    push_sfx("coins")
    push_dice("Aldric — Intimidation:  d20 + 3 = 19  →  The barkeep's jaw tightens.")
    time.sleep(2.8)
    push_text('"You didn\'t hear it from me — that\'s Coble Ashen\'s dock. And Coble doesn\'t like questions."')
    time.sleep(3.5)

    # ── Overcast, ambush, HP damage ───────────────────────────────────────────
    push_world_time("3 Ashveil 1312 AR", "Moonday", "midday", "Long Hollow", "overcast")
    time.sleep(1.8)
    push_text("The ambush comes fast — two figures drop from the rafters, blades already drawn.")
    time.sleep(3.0)
    push_sfx("sword")
    push_text("Steel clashes. Aldric takes a cut across the ribs — not deep, but it burns.")
    time.sleep(3.0)
    push_hp("Aldric", 11, 28)
    time.sleep(0.4)

    # ── Combat tracker ────────────────────────────────────────────────────────
    push_turn_order(["Aldric", "Cutthroat", "Pirate"], "Aldric", 2)
    time.sleep(2.0)
    push_sfx("impact")
    push_text("He drives his elbow into the nearest man's throat. The cutthroat staggers back into the bar stools.")
    time.sleep(2.8)
    push_dice("Aldric — Attack:  d20 + 5 = 21 vs AC 14  →  Hit!   1d8 + 3 = 9 slashing")
    time.sleep(2.4)

    push_turn_current("Cutthroat")
    time.sleep(1.8)
    push_sfx("shout")
    push_text('The pirate circles left — "End of the road, soldier" — then feints high.')
    time.sleep(2.8)
    push_dice("Cutthroat — Attack:  d20 + 3 = 11 vs AC 17  →  Miss")
    time.sleep(2.2)

    push_turn_current("Pirate")
    time.sleep(1.4)
    push_turn_current("Aldric", round_num=3)
    time.sleep(1.6)

    # ── Combat ends ───────────────────────────────────────────────────────────
    push_sfx("thud")
    push_text("The second man breaks first — scrambles out the door and doesn't look back.")
    time.sleep(3.0)
    push_text("Aldric lets him go. The barkeep has already disappeared.")
    time.sleep(2.8)
    push_turn_clear()

    # ── Evening cool-down ─────────────────────────────────────────────────────
    push_world_time("3 Ashveil 1312 AR", "Moonday", "evening", "Long Hollow", "calm")
    time.sleep(2.0)
    push_text("Outside, the harbour goes about its business. Nobody saw a thing.")
    time.sleep(4.5)


def demo_loop():
    time.sleep(1.5)   # wait for Flask
    print("Demo loop running — open http://localhost:5002")
    while True:
        try:
            run_sequence()
        except Exception as e:
            print(f"Demo loop error: {e}")
            time.sleep(2.0)
        time.sleep(1.5)   # brief pause before restart


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    threading.Thread(target=demo_loop, daemon=True).start()
    print("Starting demo server on http://localhost:5002 …")
    print("Press Ctrl-C to stop.")
    app.run(host="localhost", port=5002, threaded=True, debug=False)
