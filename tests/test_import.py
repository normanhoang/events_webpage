import json

import pytest
from django.core.management import call_command

from events.models import Event, Occurrence

pytestmark = pytest.mark.django_db


def sample_event():
    return {
        "title": "Test-only gallery", "description": "Synthetic fixture, not a real event.",
        "category": "art", "tags": ["Art"], "official_url": "https://example.org/test-gallery",
        "price_min": "0.00", "price_max": "0.00", "price_label": "Free",
        "verified_at": "2030-01-01T12:00:00-05:00",
        "occurrences": [{"source_key": "session-a", "starts_at": "2030-05-01T18:00:00-04:00",
                         "ends_at": "2030-05-01T20:00:00-04:00"}],
    }


def test_import_reruns_update_stable_source_keys_without_duplicates(tmp_path):
    path = tmp_path / "events.json"
    records = [sample_event()]
    path.write_text(json.dumps(records))
    call_command("import_events", str(path))
    event = Event.objects.get()
    occurrence = Occurrence.objects.get()
    slug = event.slug
    call_command("import_events", str(path))
    assert Event.objects.count() == Occurrence.objects.count() == 1
    records[0]["title"] = "Updated gallery title"
    records[0]["occurrences"][0]["starts_at"] = "2030-05-01T19:00:00-04:00"
    path.write_text(json.dumps(records))
    call_command("import_events", str(path))
    event.refresh_from_db()
    occurrence.refresh_from_db()
    assert Event.objects.count() == Occurrence.objects.count() == 1
    assert event.title == "Updated gallery title"
    assert event.slug == slug
    assert event.tags == ["Art"]
    assert event.verified_at is not None
    assert occurrence.starts_at.hour == 23


def test_invalid_import_is_atomic_and_reports_actionable_errors(tmp_path):
    from copy import deepcopy
    from django.core.management.base import CommandError

    path = tmp_path / "bad.json"
    variants = [
        {"unexpected_field": "typo"}, {"official_url": "javascript:alert(1)"},
        {"tags": "not a list"}, {"tags": [42]}, {"currency": "usd"},
        {"occurrences": [{"source_key": "x", "starts_at": "2030-05-01T18:00:00"}]},
        {"occurrences": [{"source_key": "x", "starts_at": "not a date"}]},
        {"occurrences": [{"source_key": "x", "starts_at": "2030-05-01T18:00:00Z", "typo": 1}]},
        {"price_min": "20", "price_max": "10"}, {"top_pick": "false"},
    ]
    for changes in variants:
        bad = deepcopy(sample_event())
        bad["official_url"] = "https://example.org/second"
        bad.update(changes)
        path.write_text(json.dumps([sample_event(), bad]))
        with pytest.raises(CommandError, match="record 2"):
            call_command("import_events", str(path))
        assert Event.objects.count() == Occurrence.objects.count() == 0
    for contents in ["{}", "[1]", "broken JSON"]:
        path.write_text(contents)
        with pytest.raises(CommandError):
            call_command("import_events", str(path))
    with pytest.raises(CommandError):
        call_command("import_events", str(tmp_path / "missing.json"))


def test_repository_seed_contains_verified_events_and_import_is_idempotent():
    from django.conf import settings

    records = json.loads((settings.BASE_DIR / "data/events.json").read_text())
    assert 12 <= len(records) <= 20
    assert len({record["official_url"] for record in records}) == len(records)
    assert all(record["verified_at"].startswith("2026-09-13") for record in records)
    assert {"theater", "queer", "tech", "comics", "gaming", "books", "fitness"} <= {
        record["category"] for record in records
    }
    call_command("import_events")
    call_command("import_events")
    assert Event.objects.count() == len(records)
    assert Occurrence.objects.count() >= len(records)
