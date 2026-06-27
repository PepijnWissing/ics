import hashlib
import os
import sys

import requests
from icalendar import Calendar, vText

from config import CALENDARS as CALENDAR_CONFIG

CALENDARS = []
for _i, _cfg in enumerate(CALENDAR_CONFIG, 1):
    _url = os.getenv(f"ICAL{_i}")
    if not _url:
        continue
    CALENDARS.append({
        "url": _url.replace("webcal://", "https://", 1),
        "color": _cfg.get("color"),
        "prefix": _cfg.get("prefix"),
    })

if not CALENDARS:
    print("No calendars found. Set ICAL1..ICAL5 environment variables.")
    sys.exit(1)

merged = Calendar()
merged.add("prodid", vText("-//Frameo Calendar//"))
merged.add("version", "2.0")
merged.add("calscale", "GREGORIAN")
merged.add("method", "PUBLISH")

timezones: dict[str, object] = {}
events: list[tuple[str, str | None, object, dict]] = []

session = requests.Session()
failed: list[str] = []

for cal_config in CALENDARS:
    url = cal_config["url"]
    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()
    except Exception as exc:
        print(f"Warning: could not fetch calendar: {exc}", file=sys.stderr)
        failed.append(url)
        continue

    try:
        cal = Calendar.from_ical(response.content)
    except Exception as exc:
        print(f"Warning: could not parse calendar: {exc}", file=sys.stderr)
        failed.append(url)
        continue

    for component in cal.walk():
        if component.name == "VTIMEZONE":
            tzid = str(component.get("TZID", ""))
            if tzid and tzid not in timezones:
                timezones[tzid] = component

        elif component.name == "VEVENT":
            uid = str(component.get("UID", ""))
            recurrence_id = component.get("RECURRENCE-ID")
            recurrence_id_str = str(recurrence_id) if recurrence_id is not None else None
            events.append((uid, recurrence_id_str, component, cal_config))

if len(failed) == len(CALENDARS):
    print("All calendar feeds failed. Keeping existing combined.ics.", file=sys.stderr)
    sys.exit(1)

seen: set[tuple[str, str | None]] = set()

for tzcomp in timezones.values():
    merged.add_component(tzcomp)

for uid, recurrence_id, component, cal_config in events:
    key = (uid, recurrence_id)
    if key in seen:
        continue
    seen.add(key)

    if cal_config["color"]:
        if "COLOR" in component:
            del component["COLOR"]
        component.add("COLOR", cal_config["color"])

    if cal_config["prefix"]:
        summary = str(component.get("SUMMARY", ""))
        if "SUMMARY" in component:
            del component["SUMMARY"]
        component.add("SUMMARY", f"{cal_config['prefix']} {summary}".strip())

    merged.add_component(component)

output = merged.to_ical()

os.makedirs("docs", exist_ok=True)
outfile = "docs/combined.ics"

new_hash = hashlib.sha256(output).hexdigest()
old_hash = None
if os.path.exists(outfile):
    with open(outfile, "rb") as f:
        old_hash = hashlib.sha256(f.read()).hexdigest()

if new_hash != old_hash:
    with open(outfile, "wb") as f:
        f.write(output)
    print(f"Calendar updated ({len(seen)} events).")
else:
    print("No changes.")
