from datetime import datetime
from zoneinfo import ZoneInfo

import pytest


def occurrence(key, starts, ends=None):
    item = {"source_key": key, "starts_at": starts}
    if ends is not None:
        item["ends_at"] = ends
    return item


def event(title, *occurrences):
    return {
        "title": title,
        "official_url": f"https://example.org/{title.lower().replace(' ', '-')}",
        "occurrences": list(occurrences),
    }


def test_archive_past_records_preserves_future_dates_and_uses_end_of_day_for_missing_ends():
    from events.catalog import archive_past_records

    records = [
        event(
            "Mixed",
            occurrence("ended", "2030-05-01T10:00:00-04:00", "2030-05-01T12:00:00-04:00"),
            occurrence("today-no-end", "2030-05-02T09:00:00-04:00"),
            occurrence("future", "2030-05-03T09:00:00-04:00"),
        ),
        event("Past", occurrence("yesterday-no-end", "2030-05-01T20:00:00-04:00")),
    ]
    now = datetime(2030, 5, 2, 12, tzinfo=ZoneInfo("America/New_York"))

    active, archived = archive_past_records(records, now=now)

    assert [item["title"] for item in active] == ["Mixed"]
    assert [item["source_key"] for item in active[0]["occurrences"]] == ["today-no-end", "future"]
    assert [item["title"] for item in archived] == ["Mixed", "Past"]
    assert [item["source_key"] for item in archived[0]["occurrences"]] == ["ended"]
    assert archived[0]["archive_reason"] == "past"
    assert archived[1]["occurrences"][0]["source_key"] == "yesterday-no-end"


def test_archive_events_command_writes_monthly_history_idempotently(tmp_path):
    import json
    from django.core.management import call_command

    active_path = tmp_path / "events.json"
    archive_dir = tmp_path / "archive"
    active_path.write_text(json.dumps([
        event(
            "Mixed",
            occurrence("past", "2030-05-01T10:00:00-04:00", "2030-05-01T12:00:00-04:00"),
            occurrence("future", "2030-05-03T10:00:00-04:00"),
        ),
        event("Past", occurrence("old", "2030-04-30T10:00:00-04:00")),
    ]))

    options = {
        "active": str(active_path),
        "archive_dir": str(archive_dir),
        "now": "2030-05-02T12:00:00-04:00",
    }
    call_command("archive_events", **options)
    call_command("archive_events", **options)

    active = json.loads(active_path.read_text())
    archived = json.loads((archive_dir / "2030-05.json").read_text())
    assert [item["title"] for item in active] == ["Mixed"]
    assert active[0]["occurrences"][0]["source_key"] == "future"
    assert [(item["title"], item["occurrences"][0]["source_key"]) for item in archived] == [
        ("Mixed", "past"),
        ("Past", "old"),
    ]


def test_archive_failure_cannot_remove_active_data_before_history_is_saved(tmp_path, monkeypatch):
    import json
    from django.core.management import call_command
    from events.management.commands import archive_events

    active_path = tmp_path / "events.json"
    archive_dir = tmp_path / "archive"
    original = [
        event(
            "Mixed",
            occurrence("past", "2030-05-01T10:00:00-04:00", "2030-05-01T12:00:00-04:00"),
            occurrence("future", "2030-05-03T10:00:00-04:00"),
        )
    ]
    active_path.write_text(json.dumps(original))
    real_write = archive_events.write_json_atomic
    calls = []

    def fail_second_write(path, records):
        calls.append(path)
        if len(calls) == 2:
            raise OSError("simulated active-file replacement failure")
        real_write(path, records)

    monkeypatch.setattr(archive_events, "write_json_atomic", fail_second_write)
    with pytest.raises(OSError, match="simulated"):
        call_command(
            "archive_events",
            active=str(active_path),
            archive_dir=str(archive_dir),
            now="2030-05-02T12:00:00-04:00",
        )

    assert json.loads(active_path.read_text()) == original
    assert calls[0] == archive_dir / "2030-05.json"


def test_archive_lock_refuses_a_preexisting_symbolic_link(tmp_path, monkeypatch):
    import json
    from django.core.management import call_command
    from events.management.commands.archive_events import archive_lock_path

    runtime = tmp_path / "runtime"
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    active_path = tmp_path / "events.json"
    active_path.write_text(json.dumps([event("Past", occurrence("old", "2030-05-01T10:00:00-04:00"))]))
    victim = tmp_path / "victim.txt"
    victim.write_text("do not truncate")
    lock_path = archive_lock_path(active_path)
    lock_path.symlink_to(victim)

    with pytest.raises(OSError):
        call_command(
            "archive_events",
            active=str(active_path),
            archive_dir=str(tmp_path / "archive"),
            now="2030-05-02T12:00:00-04:00",
        )

    assert victim.read_text() == "do not truncate"
