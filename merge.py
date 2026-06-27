import hashlib
import os
import sys

import requests
from icalendar import Calendar, vText

URLS = [
    os.getenv("ICAL1"),
    os.getenv("ICAL2"),
    os.getenv("ICAL3"),
    os.getenv("ICAL4"),
]

URLS = [u for u in URLS if u]

if not URLS:
    print("No calendars found. Set ICAL1..ICAL4 environment variables.")
    sys.exit(1)

merged = Calendar()
merged.add("prodid", vText("-//Frameo Calendar//"))
merged.add("version", "2.0")
merged.add("calscale", "GREGORIAN")
merged.add("method", "PUBLISH")

# Collect VTIMEZONE components (keyed by TZID) and VEVENT components.
# VTIMEZONE must appear before any events that reference them.
timezones: dict[str, object] = {}
events: list[tuple[str, str | None, object]] = []  # (uid, recurrence_id, component)

session = requests.Session()
failed_urls: list[str] = []

for url in URLS:
    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()
    except Exception as exc:
        print(f"Warning: could not fetch {url!r}: {exc}", file=sys.stderr)
        failed_urls.append(url)
        continue

    try:
        cal = Calendar.from_ical(response.content)
    except Exception as exc:
        print(f"Warning: could not parse calendar from {url!r}: {exc}", file=sys.stderr)
        failed_urls.append(url)
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
            events.append((uid, recurrence_id_str, component))

# Abort if every feed failed — keep the existing combined.ics intact.
if len(failed_urls) == len(URLS):
    print("All calendar feeds failed. Keeping existing combined.ics.", file=sys.stderr)
    sys.exit(1)

# Deduplicate: a regular event is identified by (uid).
# An exception to a recurring event is identified by (uid, recurrence_id).
seen: set[tuple[str, str | None]] = set()

for tzcomp in timezones.values():
    merged.add_component(tzcomp)

for uid, recurrence_id, component in events:
    key = (uid, recurrence_id)
    if key in seen:
        continue
    seen.add(key)
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