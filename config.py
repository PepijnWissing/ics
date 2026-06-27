# One entry per calendar slot, matching ICAL1 through ICAL5.
# color  – RFC 7986 CSS named color applied to every event (or None)
# prefix – text/emoji prepended to every event summary (or None)
CALENDARS = [
    {"color": "yellow",  "prefix": None},  # ICAL1 - s
    {"color": "red",     "prefix": None},  # ICAL2 - f
    {"color": "blue",    "prefix": None},  # ICAL3 - p
    {"color": "lime",    "prefix": None},  # ICAL4 - a
    {"color": "purple",  "prefix": "🎂"},  # ICAL5 – birthdays
]
