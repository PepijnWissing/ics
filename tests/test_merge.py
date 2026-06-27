"""Tests for the ICS merge logic."""

import hashlib
import os
import sys
import textwrap
from unittest.mock import MagicMock, patch

import pytest
from icalendar import Calendar

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_ics(*summaries: str, tzid: str | None = None) -> bytes:
    """Return a minimal ICS file containing one VEVENT per summary."""
    lines = [
        "BEGIN:VCALENDAR",
        "PRODID:-//Test//",
        "VERSION:2.0",
    ]
    if tzid:
        lines += [
            f"BEGIN:VTIMEZONE",
            f"TZID:{tzid}",
            "BEGIN:STANDARD",
            "TZOFFSETFROM:+0100",
            "TZOFFSETTO:+0000",
            "DTSTART:19701025T030000",
            "END:STANDARD",
            "END:VTIMEZONE",
        ]
    for i, summary in enumerate(summaries):
        lines += [
            "BEGIN:VEVENT",
            f"UID:event-{i}-{summary}@test",
            f"SUMMARY:{summary}",
            "DTSTART:20260101T090000Z",
            "DTEND:20260101T100000Z",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines).encode()


def make_recurring_exception(uid: str, recurrence_id: str, summary: str) -> bytes:
    """Return an ICS containing a RECURRENCE-ID exception."""
    return textwrap.dedent(f"""\
        BEGIN:VCALENDAR
        PRODID:-//Test//
        VERSION:2.0
        BEGIN:VEVENT
        UID:{uid}
        RECURRENCE-ID:20260108T090000Z
        SUMMARY:{summary}
        DTSTART:{recurrence_id}
        DTEND:20260108T100000Z
        END:VEVENT
        END:VCALENDAR
    """).encode()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ("ICAL1", "ICAL2", "ICAL3", "ICAL4"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture()
def tmp_docs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Unit-level helpers to avoid re-running the full script
# ---------------------------------------------------------------------------

def run_merge(feeds: dict[str, bytes], monkeypatch, tmp_docs) -> Calendar:
    """
    Simulate merge.py by patching env vars and requests, then importing
    the logic extracted into a callable. Because merge.py uses module-level
    code we re-import it fresh each time.
    """
    import importlib
    import types

    for i, (url, _) in enumerate(feeds.items(), 1):
        monkeypatch.setenv(f"ICAL{i}", url)

    def fake_get(url, timeout=30):
        resp = MagicMock()
        resp.content = feeds[url]
        resp.raise_for_status = lambda: None
        return resp

    session_mock = MagicMock()
    session_mock.get.side_effect = fake_get

    # We execute merge.py as a script in a subprocess-like fashion via exec
    # so each test gets a clean module-level state.
    merge_source = open(os.path.join(os.path.dirname(__file__), "..", "merge.py")).read()

    # Patch requests.Session inside the exec context
    import requests as req_mod

    original_session = req_mod.Session

    class FakeSession:
        def get(self, url, timeout=30):
            return fake_get(url, timeout)

    req_mod.Session = FakeSession  # type: ignore[attr-defined]
    try:
        exec(compile(merge_source, "merge.py", "exec"), {"__name__": "__main__"})
    except SystemExit as exc:
        if exc.code not in (0, None):
            raise
    finally:
        req_mod.Session = original_session  # type: ignore[attr-defined]

    with open(tmp_docs / "docs" / "combined.ics", "rb") as f:
        return Calendar.from_ical(f.read())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBasicMerge:
    def test_events_from_two_feeds_are_combined(self, monkeypatch, tmp_docs):
        feeds = {
            "https://cal1.example": make_ics("Work meeting"),
            "https://cal2.example": make_ics("Birthday"),
        }
        cal = run_merge(feeds, monkeypatch, tmp_docs)
        summaries = {
            str(c.get("SUMMARY")) for c in cal.walk() if c.name == "VEVENT"
        }
        assert summaries == {"Work meeting", "Birthday"}

    def test_duplicate_uid_appears_only_once(self, monkeypatch, tmp_docs):
        shared_uid_ics = (
            "BEGIN:VCALENDAR\r\n"
            "PRODID:-//Test//\r\n"
            "VERSION:2.0\r\n"
            "BEGIN:VEVENT\r\n"
            "UID:shared-uid@test\r\n"
            "SUMMARY:Duplicate\r\n"
            "DTSTART:20260101T090000Z\r\n"
            "DTEND:20260101T100000Z\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        ).encode()
        feeds = {
            "https://cal1.example": shared_uid_ics,
            "https://cal2.example": shared_uid_ics,
        }
        cal = run_merge(feeds, monkeypatch, tmp_docs)
        events = [c for c in cal.walk() if c.name == "VEVENT"]
        assert len(events) == 1

    def test_no_change_leaves_file_identical(self, monkeypatch, tmp_docs):
        feeds = {"https://cal1.example": make_ics("Stable event")}
        cal1 = run_merge(feeds, monkeypatch, tmp_docs)

        first = (tmp_docs / "docs" / "combined.ics").read_bytes()
        cal2 = run_merge(feeds, monkeypatch, tmp_docs)
        second = (tmp_docs / "docs" / "combined.ics").read_bytes()

        assert first == second


class TestTimezones:
    def test_vtimezone_is_included_in_output(self, monkeypatch, tmp_docs):
        feeds = {"https://cal1.example": make_ics("TZ event", tzid="Europe/Amsterdam")}
        cal = run_merge(feeds, monkeypatch, tmp_docs)
        tzids = {
            str(c.get("TZID")) for c in cal.walk() if c.name == "VTIMEZONE"
        }
        assert "Europe/Amsterdam" in tzids

    def test_duplicate_timezones_appear_only_once(self, monkeypatch, tmp_docs):
        feeds = {
            "https://cal1.example": make_ics("Event A", tzid="Europe/Amsterdam"),
            "https://cal2.example": make_ics("Event B", tzid="Europe/Amsterdam"),
        }
        cal = run_merge(feeds, monkeypatch, tmp_docs)
        tz_components = [c for c in cal.walk() if c.name == "VTIMEZONE"]
        assert len(tz_components) == 1


class TestRecurrenceExceptions:
    def test_recurrence_exception_is_preserved(self, monkeypatch, tmp_docs):
        uid = "recurring@test"
        base_ics = (
            "BEGIN:VCALENDAR\r\n"
            "PRODID:-//Test//\r\n"
            "VERSION:2.0\r\n"
            "BEGIN:VEVENT\r\n"
            f"UID:{uid}\r\n"
            "SUMMARY:Weekly standup\r\n"
            "DTSTART:20260101T090000Z\r\n"
            "DTEND:20260101T100000Z\r\n"
            "RRULE:FREQ=WEEKLY\r\n"
            "END:VEVENT\r\n"
            "END:VCALENDAR\r\n"
        ).encode()
        exception_ics = make_recurring_exception(uid, "20260108T100000Z", "Standup moved")
        feeds = {
            "https://cal1.example": base_ics,
            "https://cal2.example": exception_ics,
        }
        cal = run_merge(feeds, monkeypatch, tmp_docs)
        summaries = [str(c.get("SUMMARY")) for c in cal.walk() if c.name == "VEVENT"]
        assert "Weekly standup" in summaries
        assert "Standup moved" in summaries


class TestErrorHandling:
    def test_one_failed_feed_does_not_abort(self, monkeypatch, tmp_docs):
        import requests as req_mod

        good_ics = make_ics("Good event")

        original_session = req_mod.Session

        class PatchedSession:
            def get(self, url, timeout=30):
                if url == "https://bad.example":
                    raise ConnectionError("timeout")
                resp = MagicMock()
                resp.content = good_ics
                resp.raise_for_status = lambda: None
                return resp

        req_mod.Session = PatchedSession  # type: ignore[attr-defined]
        monkeypatch.setenv("ICAL1", "https://good.example")
        monkeypatch.setenv("ICAL2", "https://bad.example")

        try:
            merge_source = open(
                os.path.join(os.path.dirname(__file__), "..", "merge.py")
            ).read()
            exec(compile(merge_source, "merge.py", "exec"), {"__name__": "__main__"})
        except SystemExit as exc:
            assert exc.code in (0, None), f"Script exited with {exc.code}"
        finally:
            req_mod.Session = original_session  # type: ignore[attr-defined]

        cal = Calendar.from_ical(
            (tmp_docs / "docs" / "combined.ics").read_bytes()
        )
        summaries = [str(c.get("SUMMARY")) for c in cal.walk() if c.name == "VEVENT"]
        assert "Good event" in summaries

    def test_all_feeds_failing_exits_nonzero(self, monkeypatch, tmp_docs):
        import requests as req_mod

        original_session = req_mod.Session

        class FailSession:
            def get(self, url, timeout=30):
                raise ConnectionError("all down")

        req_mod.Session = FailSession  # type: ignore[attr-defined]
        monkeypatch.setenv("ICAL1", "https://fail1.example")

        try:
            merge_source = open(
                os.path.join(os.path.dirname(__file__), "..", "merge.py")
            ).read()
            with pytest.raises(SystemExit) as exc_info:
                exec(
                    compile(merge_source, "merge.py", "exec"),
                    {"__name__": "__main__"},
                )
            assert exc_info.value.code == 1
        finally:
            req_mod.Session = original_session  # type: ignore[attr-defined]

    def test_no_env_vars_exits_nonzero(self, monkeypatch, tmp_docs):
        merge_source = open(
            os.path.join(os.path.dirname(__file__), "..", "merge.py")
        ).read()
        with pytest.raises(SystemExit) as exc_info:
            exec(compile(merge_source, "merge.py", "exec"), {"__name__": "__main__"})
        assert exc_info.value.code == 1
