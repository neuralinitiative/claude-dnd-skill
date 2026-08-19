#!/usr/bin/env python3
"""
calendar.py — in-world date and time manager

Handles time advancement for any campaign calendar — including fully custom
systems (Harvestmoon, Deepwinter, etc.). Stores the current date/time as a
structured object so arithmetic is always consistent.

The calendar is defined per-campaign in world.md under ## World Foundations →
Calendar. Run `calendar.py init` once to register it; all subsequent commands
use the stored definition.

Usage:
    # One-time setup (from the world.md calendar block):
    python3 calendar.py -c <campaign> init \
        --date "15 Harvestmoon 1247" \
        --time "morning" \
        --months "Frostfall,Deepwinter,Thawmonth,Seedtime,Bloomtide,Highsun,Harvestmoon,Duskfall" \
        --month-length 30 \
        --day-names "Sunday,Moonday,Ironday,Windday,Earthday,Fireday,Starday"

    # Advance time
    python3 calendar.py -c <campaign> advance 8 hours
    python3 calendar.py -c <campaign> advance 2 days
    python3 calendar.py -c <campaign> advance 1 week

    # Rest shortcuts
    python3 calendar.py -c <campaign> rest short    # +1 hour
    python3 calendar.py -c <campaign> rest long     # +8 hours

    # Show current date/time
    python3 calendar.py -c <campaign> now

    # Set date/time directly (use after manual world.md edits)
    python3 calendar.py -c <campaign> set "22 Harvestmoon 1247" midday

    # Time of day only
    python3 calendar.py -c <campaign> time <morning|midday|afternoon|evening|night|midnight>

    # List upcoming events (from world.md — entered at init or updated manually)
    python3 calendar.py -c <campaign> events
"""

import json
import os
import sys
import subprocess
import argparse
from paths import find_campaign as _find_campaign, display_dir as _display_dir

SEND_PY = str(_display_dir() / "send.py")

# Time of day labels and approximate hour ranges
TIMES_OF_DAY = [
    ("midnight",    0,  1),
    ("early morning", 1, 5),
    ("morning",     6,  10),
    ("midday",      11, 13),
    ("afternoon",   14, 17),
    ("evening",     18, 21),
    ("night",       22, 23),
]

HOURS_PER_TIME = {
    "midnight":      0,
    "early morning": 3,
    "morning":       8,
    "midday":        12,
    "afternoon":     15,
    "evening":       19,
    "night":         22,
}


def _cal_path(campaign: str) -> str:
    d = str(_find_campaign(campaign))
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "calendar.json")


def _load(campaign: str) -> dict:
    try:
        with open(_cal_path(campaign), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(campaign: str, cal: dict) -> None:
    with open(_cal_path(campaign), "w", encoding="utf-8") as f:
        json.dump(cal, f, indent=2, ensure_ascii=False)


def _month_length(cal: dict) -> int:
    return cal.get("month_length", 30)


def _month_list(cal: dict) -> list[str]:
    return cal.get("months", [])


def _day_names(cal: dict) -> list[str]:
    return cal.get("day_names", [])


def _format_date(cal: dict) -> str:
    """Format current date as a human-readable string."""
    day    = cal.get("day", 1)
    month  = cal.get("month", 1)
    year   = cal.get("year", 1)
    hour   = cal.get("hour", 8)
    months = _month_list(cal)
    days   = _day_names(cal)

    month_str = months[month - 1] if months and 1 <= month <= len(months) else f"Month {month}"
    day_str   = f"Day {day}"
    if days:
        day_of_week = (day - 1) % len(days)
        day_str = days[day_of_week]

    # Map hour to time-of-day label
    tod = "night"
    for label, lo, hi in TIMES_OF_DAY:
        if lo <= hour <= hi:
            tod = label
            break

    return f"{day_str}, {day} {month_str} {year} — {tod} (hour {hour})"


def _advance_hours(cal: dict, hours: int) -> None:
    """Advance the calendar by a given number of hours."""
    cal.setdefault("hour", 8)
    cal.setdefault("day", 1)
    cal.setdefault("month", 1)
    cal.setdefault("year", 1)

    cal["hour"] += hours
    month_len = _month_length(cal)

    # Roll over hours → days
    while cal["hour"] >= 24:
        cal["hour"] -= 24
        cal["day"] += 1

    # Roll over days → months
    months = _month_list(cal)
    num_months = len(months) if months else 12
    while cal["day"] > month_len:
        cal["day"] -= month_len
        cal["month"] += 1
        if cal["month"] > num_months:
            cal["month"] = 1
            cal["year"] += 1


def _send_date(cal: dict) -> None:
    """Push the current date announcement to the display."""
    msg = f"📅 {_format_date(cal)}"
    try:
        proc = subprocess.Popen(
            [sys.executable, SEND_PY, "--dice"],
            stdin=subprocess.PIPE, capture_output=True, timeout=3,
        )
        proc.communicate(input=msg.encode())
    except Exception:
        pass


# ─── Commands ────────────────────────────────────────────────────────────────

def cmd_init(campaign: str, args) -> None:
    cal: dict = {}

    # Parse the starting date string: "15 Harvestmoon 1247"
    date_str = args.date
    try:
        parts = date_str.split()
        if len(parts) >= 3:
            month_name = parts[1]
            cal["day"]  = int(parts[0])
            cal["year"] = int(parts[2])
        elif len(parts) == 2:
            month_name = parts[1]
            cal["day"]  = int(parts[0])
            cal["year"] = 1
        else:
            cal["day"]  = 1
            month_name  = date_str
            cal["year"] = 1

        # Figure out month index
        months = [m.strip() for m in args.months.split(",") if m.strip()] if args.months else []
        cal["months"] = months
        cal["month"] = 1
        for i, m in enumerate(months):
            if m.lower() == month_name.lower():
                cal["month"] = i + 1
                break
    except (ValueError, IndexError):
        cal.update({"day": 1, "month": 1, "year": 1, "months": []})

    cal["hour"]        = HOURS_PER_TIME.get(args.time or "morning", 8)
    cal["month_length"] = int(args.month_length) if args.month_length else 30
    cal["day_names"]   = [d.strip() for d in args.day_names.split(",") if d.strip()] if args.day_names else []
    cal["events"]      = []

    _save(campaign, cal)
    print(f"Calendar initialised: {_format_date(cal)}")


def cmd_advance(campaign: str, amount: int, unit: str) -> None:
    cal = _load(campaign)
    if not cal:
        print("Calendar not initialised. Run `calendar.py -c <campaign> init` first.")
        sys.exit(1)

    hours = {"hour": 1, "hours": 1, "day": 24, "days": 24, "week": 168, "weeks": 168}.get(unit, 1)
    total_hours = amount * hours
    _advance_hours(cal, total_hours)
    _save(campaign, cal)

    label = f"+{amount} {unit}"
    print(f"  {label} → {_format_date(cal)}")
    _send_date(cal)


def cmd_rest(campaign: str, rest_type: str) -> None:
    cal = _load(campaign)
    if not cal:
        print("Calendar not initialised. Run `calendar.py -c <campaign> init` first.")
        sys.exit(1)

    if rest_type == "short":
        _advance_hours(cal, 1)
        label = "Short rest (+1 hour)"
    else:
        _advance_hours(cal, 8)
        label = "Long rest (+8 hours)"

    _save(campaign, cal)
    print(f"  {label} → {_format_date(cal)}")
    _send_date(cal)


def cmd_now(campaign: str) -> None:
    cal = _load(campaign)
    if not cal:
        print("Calendar not initialised. Run `calendar.py -c <campaign> init` first.")
    else:
        print(_format_date(cal))


def cmd_set(campaign: str, date_str: str, time_str: str) -> None:
    cal = _load(campaign)
    if not cal:
        cal = {"months": [], "day_names": [], "month_length": 30, "events": []}

    months = cal.get("months", [])
    try:
        parts = date_str.split()
        if len(parts) >= 3:
            month_name = parts[1]
            cal["day"]  = int(parts[0])
            cal["year"] = int(parts[2])
            cal["month"] = 1
            for i, m in enumerate(months):
                if m.lower() == month_name.lower():
                    cal["month"] = i + 1
                    break
        elif len(parts) == 1:
            cal["day"] = int(parts[0])
    except (ValueError, IndexError):
        pass

    if time_str:
        cal["hour"] = HOURS_PER_TIME.get(time_str.lower(), cal.get("hour", 8))

    _save(campaign, cal)
    print(f"  Date set: {_format_date(cal)}")
    _send_date(cal)


def cmd_time(campaign: str, time_str: str) -> None:
    cal = _load(campaign)
    if not cal:
        print("Calendar not initialised.")
        sys.exit(1)
    cal["hour"] = HOURS_PER_TIME.get(time_str.lower(), cal.get("hour", 8))
    _save(campaign, cal)
    print(f"  Time set: {_format_date(cal)}")
    _send_date(cal)


def cmd_events(campaign: str) -> None:
    cal = _load(campaign)
    events = cal.get("events", [])
    if not events:
        print("  No upcoming events registered.")
        print("  Add events by editing ~/.claude/dnd/campaigns/<name>/calendar.json")
        print('  Events format: [{"name": "Festival of Stars", "date": "1 Bloomtide 1248"}]')
    else:
        print("Upcoming events:")
        for e in events:
            print(f"  {e.get('date','?')} — {e.get('name','?')}")


# ─── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="In-world date and time manager.")
    p.add_argument("-c", "--campaign", required=True, metavar="NAME")
    sub = p.add_subparsers(dest="cmd")

    ini = sub.add_parser("init", help="Initialise campaign calendar (run once)")
    ini.add_argument("--date",         default="1 Month 1",
                     help="Starting date, e.g. '15 Harvestmoon 1247'")
    ini.add_argument("--time",         default="morning",
                     help="Starting time of day (morning/midday/afternoon/evening/night/midnight)")
    ini.add_argument("--months",       default="",
                     help="Comma-separated month names in order")
    ini.add_argument("--month-length", default="30",
                     help="Days per month (uniform, default 30)")
    ini.add_argument("--day-names",    default="",
                     help="Comma-separated day-of-week names")

    adv = sub.add_parser("advance", help="Advance time by amount")
    adv.add_argument("amount", type=int)
    adv.add_argument("unit",
                     choices=["hour","hours","day","days","week","weeks"])

    rst = sub.add_parser("rest", help="Advance time for a short or long rest")
    rst.add_argument("type", choices=["short","long"])

    sub.add_parser("now", help="Print current date/time")

    st = sub.add_parser("set", help="Set the current date/time directly")
    st.add_argument("date",      help="Date string, e.g. '22 Harvestmoon 1247'")
    st.add_argument("time", nargs="?", default="",
                    help="Time of day (optional)")

    tm = sub.add_parser("time", help="Set time of day without changing the date")
    tm.add_argument("tod",
                    choices=list(HOURS_PER_TIME.keys()),
                    help="Time of day label")

    sub.add_parser("events", help="List upcoming calendar events")

    args = p.parse_args()

    if   args.cmd == "init":    cmd_init(args.campaign, args)
    elif args.cmd == "advance": cmd_advance(args.campaign, args.amount, args.unit)
    elif args.cmd == "rest":    cmd_rest(args.campaign, args.type)
    elif args.cmd == "now":     cmd_now(args.campaign)
    elif args.cmd == "set":     cmd_set(args.campaign, args.date, args.time)
    elif args.cmd == "time":    cmd_time(args.campaign, args.tod)
    elif args.cmd == "events":  cmd_events(args.campaign)
    else:                       p.print_help()


if __name__ == "__main__":
    main()
