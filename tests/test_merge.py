"""Tests for the ICS merge logic."""

import os
import sys
import textwrap
import types
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from icalendar import Calendar

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_ics(*summaries: str, tzid: str | None = None) -> bytes:
    lines = [
        "BEGIN:VCALENDAR",
        "PRODID:-//Test//",
        "VERSION:2.0",
    ]
    if tzid:
        lines += [
            "BEGIN:VTIMEZONE",
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


def _merge_source():
    path = os.path.join(os.path.dirname(__file__), "..", "merge.py")
    with open(path) as f:
        return f.read()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for i in range(1, 6):
        monkeypatch.delenv(f"ICAL{i}", raising=False)


@pytest.fixture()
def tmp_docs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

_DEFAULT_CAL_CONFIG = {"color": None, "prefix": None}


def run_merge(
    feeds: dict[str, bytes],
    monkeypatch,
    tmp_docs,
    calendar_configs: list[dict] | None = None,
) -> Calendar:
    """
    Execute merge.py in-process with patched env vars, requests, and config.

    feeds            – maps URL → raw ICS bytes (order determines ICAL slot)
    calendar_configs – list of {"color": ..., "prefix": ...} per URL slot;
                       defaults to all-None configs
    """
    import requests as req_mod

    url_list = list(feeds.keys())
    for i, url in enumerate(url_list, 1):
        monkeypatch.setenv(f"ICAL{i}", url)

    # Build a mock config module so tests don't depend on config.py on disk.
    configs = calendar_configs or [_DEFAULT_CAL_CONFIG] * len(url_list)
    mock_config = types.ModuleType("config")
    mock_config.CALENDARS = configs  # type: ignore[attr-defined]
    sys.modules["config"] = mock_config

    original_session = req_mod.Session

    class FakeSession:
        def get(self, url, timeout=30):
            resp = MagicMock()
            resp.content = feeds[url]
            resp.raise_for_status = lambda: None
            return resp

    req_mod.Session = FakeSession  # type: ignore[attr-defined]
    try:
        exec(compile(_merge_source(), "merge.py", "exec"), {"__name__": "__main__"})
    except SystemExit as exc:
        if exc.code not in (0, None):
            raise
    finally:
        req_mod.Session = original_session  # type: ignore[attr-defined]
        sys.modules.pop("config", None)

    with open(tmp_docs / "docs" / "combined.ics", "rb") as f:
        return Calendar.from_ical(f.read())


# ---------------------------------------------------------------------------
# Tests – basic merge
# ---------------------------------------------------------------------------

class TestBasicMerge:
    def test_events_from_two_feeds_are_combined(self, monkeypatch, tmp_docs):
        feeds = {
            "https://cal1.example": make_ics("Work meeting"),
            "https://cal2.example": make_ics("Birthday"),
        }
        cal = run_merge(feeds, monkeypatch, tmp_docs)
        summaries = {str(c.get("SUMMARY")) for c in cal.walk() if c.name == "VEVENT"}
        assert summaries == {"Work meeting", "Birthday"}

    def test_five_feeds_are_combined(self, monkeypatch, tmp_docs):
        feeds = {f"https://cal{i}.example": make_ics(f"Event {i}") for i in range(1, 6)}
        cal = run_merge(feeds, monkeypatch, tmp_docs)
        summaries = {str(c.get("SUMMARY")) for c in cal.walk() if c.name == "VEVENT"}
        assert summaries == {f"Event {i}" for i in range(1, 6)}

    def test_duplicate_uid_appears_only_once(self, monkeypatch, tmp_docs):
        shared = (
            "BEGIN:VCALENDAR\r\nPRODID:-//Test//\r\nVERSION:2.0\r\n"
            "BEGIN:VEVENT\r\nUID:shared@test\r\nSUMMARY:Dup\r\n"
            "DTSTART:20260101T090000Z\r\nDTEND:20260101T100000Z\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        ).encode()
        feeds = {"https://cal1.example": shared, "https://cal2.example": shared}
        cal = run_merge(feeds, monkeypatch, tmp_docs)
        events = [c for c in cal.walk() if c.name == "VEVENT"]
        assert len(events) == 1

    def test_no_change_leaves_file_identical(self, monkeypatch, tmp_docs):
        feeds = {"https://cal1.example": make_ics("Stable event")}
        run_merge(feeds, monkeypatch, tmp_docs)
        first = (tmp_docs / "docs" / "combined.ics").read_bytes()
        run_merge(feeds, monkeypatch, tmp_docs)
        second = (tmp_docs / "docs" / "combined.ics").read_bytes()
        assert first == second


# ---------------------------------------------------------------------------
# Tests – color
# ---------------------------------------------------------------------------

class TestColor:
    def test_color_is_added_to_events(self, monkeypatch, tmp_docs):
        feeds = {"https://cal1.example": make_ics("Colored event")}
        cal = run_merge(feeds, monkeypatch, tmp_docs,
                        calendar_configs=[{"color": "red", "prefix": None}])
        events = [c for c in cal.walk() if c.name == "VEVENT"]
        assert str(events[0].get("COLOR")) == "red"

    def test_different_calendars_get_different_colors(self, monkeypatch, tmp_docs):
        feeds = {
            "https://cal1.example": make_ics("Work"),
            "https://cal2.example": make_ics("Personal"),
        }
        configs = [
            {"color": "blue",  "prefix": None},
            {"color": "green", "prefix": None},
        ]
        cal = run_merge(feeds, monkeypatch, tmp_docs, calendar_configs=configs)
        by_summary = {str(c.get("SUMMARY")): str(c.get("COLOR"))
                      for c in cal.walk() if c.name == "VEVENT"}
        assert by_summary["Work"] == "blue"
        assert by_summary["Personal"] == "green"

    def test_no_color_when_not_configured(self, monkeypatch, tmp_docs):
        feeds = {"https://cal1.example": make_ics("No color")}
        cal = run_merge(feeds, monkeypatch, tmp_docs)
        events = [c for c in cal.walk() if c.name == "VEVENT"]
        assert events[0].get("COLOR") is None


# ---------------------------------------------------------------------------
# Tests – prefix
# ---------------------------------------------------------------------------

class TestPrefix:
    def test_prefix_is_prepended_to_summary(self, monkeypatch, tmp_docs):
        feeds = {"https://cal1.example": make_ics("Standup")}
        cal = run_merge(feeds, monkeypatch, tmp_docs,
                        calendar_configs=[{"color": None, "prefix": "🏢"}])
        summaries = [str(c.get("SUMMARY")) for c in cal.walk() if c.name == "VEVENT"]
        assert summaries == ["🏢 Standup"]

    def test_prefix_and_color_can_coexist(self, monkeypatch, tmp_docs):
        feeds = {"https://cal1.example": make_ics("Meeting")}
        cal = run_merge(feeds, monkeypatch, tmp_docs,
                        calendar_configs=[{"color": "purple", "prefix": "📅"}])
        events = [c for c in cal.walk() if c.name == "VEVENT"]
        assert str(events[0].get("SUMMARY")) == "📅 Meeting"
        assert str(events[0].get("COLOR")) == "purple"

    def test_no_prefix_when_not_configured(self, monkeypatch, tmp_docs):
        feeds = {"https://cal1.example": make_ics("Clean summary")}
        cal = run_merge(feeds, monkeypatch, tmp_docs)
        summaries = [str(c.get("SUMMARY")) for c in cal.walk() if c.name == "VEVENT"]
        assert summaries == ["Clean summary"]


# ---------------------------------------------------------------------------
# Tests – timezones
# ---------------------------------------------------------------------------

class TestTimezones:
    def test_vtimezone_is_included_in_output(self, monkeypatch, tmp_docs):
        feeds = {"https://cal1.example": make_ics("TZ event", tzid="Europe/Amsterdam")}
        cal = run_merge(feeds, monkeypatch, tmp_docs)
        tzids = {str(c.get("TZID")) for c in cal.walk() if c.name == "VTIMEZONE"}
        assert "Europe/Amsterdam" in tzids

    def test_duplicate_timezones_appear_only_once(self, monkeypatch, tmp_docs):
        feeds = {
            "https://cal1.example": make_ics("Event A", tzid="Europe/Amsterdam"),
            "https://cal2.example": make_ics("Event B", tzid="Europe/Amsterdam"),
        }
        cal = run_merge(feeds, monkeypatch, tmp_docs)
        tz_components = [c for c in cal.walk() if c.name == "VTIMEZONE"]
        assert len(tz_components) == 1


# ---------------------------------------------------------------------------
# Tests – recurrence exceptions
# ---------------------------------------------------------------------------

class TestRecurrenceExceptions:
    def test_recurrence_exception_is_preserved(self, monkeypatch, tmp_docs):
        uid = "recurring@test"
        base_ics = (
            "BEGIN:VCALENDAR\r\nPRODID:-//Test//\r\nVERSION:2.0\r\n"
            "BEGIN:VEVENT\r\n"
            f"UID:{uid}\r\nSUMMARY:Weekly standup\r\n"
            "DTSTART:20260101T090000Z\r\nDTEND:20260101T100000Z\r\n"
            "RRULE:FREQ=WEEKLY\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
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


# ---------------------------------------------------------------------------
# Tests – error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def _run_with_failing_session(self, monkeypatch, tmp_docs, failing_urls: set[str],
                                   good_feeds: dict[str, bytes]) -> int | None:
        import requests as req_mod

        original_session = req_mod.Session
        all_feeds = {**good_feeds, **{u: b"" for u in failing_urls}}

        for i, url in enumerate(all_feeds, 1):
            monkeypatch.setenv(f"ICAL{i}", url)

        mock_config = types.ModuleType("config")
        mock_config.CALENDARS = [_DEFAULT_CAL_CONFIG] * len(all_feeds)  # type: ignore[attr-defined]
        sys.modules["config"] = mock_config

        class PatchedSession:
            def get(self, url, timeout=30):
                if url in failing_urls:
                    raise ConnectionError("simulated failure")
                resp = MagicMock()
                resp.content = good_feeds[url]
                resp.raise_for_status = lambda: None
                return resp

        req_mod.Session = PatchedSession  # type: ignore[attr-defined]
        exit_code = None
        try:
            exec(compile(_merge_source(), "merge.py", "exec"), {"__name__": "__main__"})
        except SystemExit as exc:
            exit_code = exc.code
        finally:
            req_mod.Session = original_session  # type: ignore[attr-defined]
            sys.modules.pop("config", None)

        return exit_code

    def test_one_failed_feed_does_not_abort(self, monkeypatch, tmp_docs):
        exit_code = self._run_with_failing_session(
            monkeypatch, tmp_docs,
            failing_urls={"https://bad.example"},
            good_feeds={"https://good.example": make_ics("Good event")},
        )
        assert exit_code in (0, None)
        cal = Calendar.from_ical((tmp_docs / "docs" / "combined.ics").read_bytes())
        summaries = [str(c.get("SUMMARY")) for c in cal.walk() if c.name == "VEVENT"]
        assert "Good event" in summaries

    def test_all_feeds_failing_exits_nonzero(self, monkeypatch, tmp_docs):
        exit_code = self._run_with_failing_session(
            monkeypatch, tmp_docs,
            failing_urls={"https://fail.example"},
            good_feeds={},
        )
        assert exit_code == 1

    def test_no_env_vars_exits_nonzero(self, monkeypatch, tmp_docs):
        mock_config = types.ModuleType("config")
        mock_config.CALENDARS = []  # type: ignore[attr-defined]
        sys.modules["config"] = mock_config
        try:
            with pytest.raises(SystemExit) as exc_info:
                exec(compile(_merge_source(), "merge.py", "exec"), {"__name__": "__main__"})
            assert exc_info.value.code == 1
        finally:
            sys.modules.pop("config", None)


# ---------------------------------------------------------------------------
# Tests – logging
# ---------------------------------------------------------------------------

class TestLogging:
    def test_log_file_is_created(self, monkeypatch, tmp_docs):
        feeds = {"https://cal1.example": make_ics("Event")}
        run_merge(feeds, monkeypatch, tmp_docs)
        assert (tmp_docs / "run.log").exists()

    def test_log_line_has_timestamp_and_message(self, monkeypatch, tmp_docs):
        feeds = {"https://cal1.example": make_ics("Event")}
        run_merge(feeds, monkeypatch, tmp_docs)
        lines = (tmp_docs / "run.log").read_text().strip().splitlines()
        assert len(lines) == 1
        # Timestamp format: 2026-06-27T12:00:00Z
        assert lines[0][10] == "T" and lines[0][19] == "Z"
        assert " | " in lines[0]

    def test_each_run_appends_a_line(self, monkeypatch, tmp_docs):
        feeds = {"https://cal1.example": make_ics("Event")}
        run_merge(feeds, monkeypatch, tmp_docs)
        run_merge(feeds, monkeypatch, tmp_docs)
        lines = (tmp_docs / "run.log").read_text().strip().splitlines()
        assert len(lines) == 2

    def test_old_entries_are_pruned(self, monkeypatch, tmp_docs):
        from datetime import timedelta, timezone
        old_ts = (datetime.now(timezone.utc) - timedelta(days=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
        log = tmp_docs / "run.log"
        log.write_text(f"{old_ts} | old entry that should be removed\n")

        feeds = {"https://cal1.example": make_ics("Event")}
        run_merge(feeds, monkeypatch, tmp_docs)

        lines = (tmp_docs / "run.log").read_text().strip().splitlines()
        assert all("old entry" not in line for line in lines)
        assert len(lines) == 1  # only the new entry

    def test_all_feeds_failing_still_writes_log(self, monkeypatch, tmp_docs):
        import requests as req_mod

        original_session = req_mod.Session

        class FailSession:
            def get(self, url, timeout=30):
                raise ConnectionError("down")

        req_mod.Session = FailSession  # type: ignore[attr-defined]
        monkeypatch.setenv("ICAL1", "https://fail.example")
        mock_config = types.ModuleType("config")
        mock_config.CALENDARS = [_DEFAULT_CAL_CONFIG]  # type: ignore[attr-defined]
        sys.modules["config"] = mock_config

        try:
            with pytest.raises(SystemExit):
                exec(compile(_merge_source(), "merge.py", "exec"), {"__name__": "__main__"})
        finally:
            req_mod.Session = original_session  # type: ignore[attr-defined]
            sys.modules.pop("config", None)

        lines = (tmp_docs / "run.log").read_text().strip().splitlines()
        assert len(lines) == 1
        assert "ERROR" in lines[0]
