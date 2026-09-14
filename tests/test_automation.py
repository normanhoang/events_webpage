from datetime import datetime
from zoneinfo import ZoneInfo

import pytest


def records(count=12):
    return [
        {
            "title": f"Event {index}",
            "description": "Verified event.",
            "category": "community",
            "tags": ["community"],
            "venue": "Venue",
            "neighborhood": "Harlem",
            "borough": "Manhattan",
            "official_url": f"https://example.org/events/{index}",
            "image_url": None,
            "price_label": "Free",
            "price_min": 0,
            "price_max": 0,
            "currency": "USD",
            "fit_reason": "Nearby and free.",
            "verified_at": "2030-05-01T12:00:00-04:00",
            "top_pick": False,
            "occurrences": [{
                "source_key": f"event-{index}",
                "starts_at": "2030-05-10T18:00:00-04:00",
                "ends_at": "2030-05-10T20:00:00-04:00",
            }],
        }
        for index in range(count)
    ]


def test_validate_catalog_enforces_the_configured_bounds():
    from automation.publish_update import MAX_EVENTS, MIN_EVENTS, TARGET_EVENTS, validate_catalog

    now = datetime(2030, 5, 2, tzinfo=ZoneInfo("America/New_York"))
    validate_catalog(records(MIN_EVENTS), now=now)
    validate_catalog(records(TARGET_EVENTS), now=now)
    validate_catalog(records(MAX_EVENTS), now=now)

    with pytest.raises(ValueError, match=f"{MIN_EVENTS} to {MAX_EVENTS}"):
        validate_catalog(records(MIN_EVENTS - 1), now=now)
    with pytest.raises(ValueError, match=f"{MIN_EVENTS} to {MAX_EVENTS}"):
        validate_catalog(records(MAX_EVENTS + 1), now=now)


def test_catalog_bounds_leave_headroom_above_the_target():
    from automation.publish_update import MAX_EVENTS, MIN_EVENTS, TARGET_EVENTS

    # Headroom above the target is what lets a festival week grow without evicting events.
    assert MIN_EVENTS < TARGET_EVENTS < MAX_EVENTS


def test_validate_catalog_caps_top_picks_so_the_headline_stays_a_curation():
    from automation.publish_update import MAX_TOP_PICKS, MIN_EVENTS, validate_catalog

    now = datetime(2030, 5, 2, tzinfo=ZoneInfo("America/New_York"))

    over = records(MIN_EVENTS)
    for record in over[: MAX_TOP_PICKS + 1]:
        record["top_pick"] = True
    with pytest.raises(ValueError, match=f"At most {MAX_TOP_PICKS} events"):
        validate_catalog(over, now=now)

    allowed = records(MIN_EVENTS)
    for record in allowed[:MAX_TOP_PICKS]:
        record["top_pick"] = True
    validate_catalog(allowed, now=now)


def test_validate_catalog_rejects_a_non_boolean_top_pick():
    from automation.publish_update import MIN_EVENTS, validate_catalog

    now = datetime(2030, 5, 2, tzinfo=ZoneInfo("America/New_York"))
    catalog = records(MIN_EVENTS)
    # A truthy string would otherwise count toward the cap while the importer rejects it.
    catalog[0]["top_pick"] = "false"

    with pytest.raises(ValueError, match="top_pick must be a JSON boolean"):
        validate_catalog(catalog, now=now)


def test_validate_catalog_requires_unique_https_official_sources():
    from automation.publish_update import validate_catalog

    now = datetime(2030, 5, 2, tzinfo=ZoneInfo("America/New_York"))
    catalog = records()
    catalog[1]["official_url"] = catalog[0]["official_url"]
    with pytest.raises(ValueError, match="unique HTTPS"):
        validate_catalog(catalog, now=now)

    catalog = records()
    catalog[0]["official_url"] = "http://example.org/insecure"
    with pytest.raises(ValueError, match="unique HTTPS"):
        validate_catalog(catalog, now=now)


def test_validate_catalog_rejects_past_out_of_horizon_and_naive_occurrences():
    from automation.publish_update import validate_catalog

    now = datetime(2030, 5, 2, tzinfo=ZoneInfo("America/New_York"))
    for invalid_start in [
        "2030-05-01T18:00:00-04:00",
        "2030-06-02T00:01:00-04:00",
        "2030-05-10T18:00:00",
    ]:
        catalog = records()
        catalog[0]["occurrences"][0]["starts_at"] = invalid_start
        catalog[0]["occurrences"][0].pop("ends_at")
        with pytest.raises(ValueError, match="occurrence"):
            validate_catalog(catalog, now=now)


def test_validate_catalog_requires_recent_nonfuture_verification():
    from automation.publish_update import validate_catalog

    now = datetime(2030, 5, 9, 12, tzinfo=ZoneInfo("America/New_York"))
    catalog = records()
    catalog[0]["verified_at"] = "2030-05-01T11:59:59-04:00"
    with pytest.raises(ValueError, match="verified_at"):
        validate_catalog(catalog, now=now)

    catalog = records()
    catalog[0]["verified_at"] = "2030-05-09T12:00:01-04:00"
    with pytest.raises(ValueError, match="verified_at"):
        validate_catalog(catalog, now=now)


def test_publish_guard_allows_only_active_and_archive_json_changes():
    from automation.publish_update import assert_only_catalog_changes

    assert_only_catalog_changes([
        "data/events.json", "data/archive/2030-05.json", "data/theater-deals.json",
        "data/theater-archive/2030-05.json",
    ])
    for path in ["README.md", "events/models.py", "data/archive/../events.json", "data/theater-archive/notes.json"]:
        with pytest.raises(ValueError, match="outside the catalog"):
            assert_only_catalog_changes([path])


def test_deployment_match_requires_exact_revision_and_catalog_count():
    from automation.publish_update import deployment_matches

    payload = {"status": "ok", "revision": "abc123", "upcoming_occurrences": 18}
    assert deployment_matches(payload, revision="abc123", minimum_count=12)
    assert not deployment_matches(payload, revision="def456", minimum_count=12)
    assert not deployment_matches(payload, revision="abc123", minimum_count=19)


def test_changed_paths_reports_tracked_and_untracked_files(tmp_path):
    import subprocess
    from automation.publish_update import changed_paths

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.org"], cwd=tmp_path, check=True)
    (tmp_path / "data").mkdir()
    (tmp_path / "data/events.json").write_text("[]\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    (tmp_path / "data/events.json").write_text("[{}]\n")
    (tmp_path / "data/archive").mkdir()
    (tmp_path / "data/archive/2030-05.json").write_text("[]\n")

    assert changed_paths(tmp_path) == ["data/archive/2030-05.json", "data/events.json"]


def test_changed_paths_includes_staged_files(tmp_path):
    import subprocess
    from automation.publish_update import changed_paths

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "README.md").write_text("initial")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)

    assert changed_paths(tmp_path) == ["README.md"]


def test_catalog_allowlist_requires_monthly_archive_filename():
    from automation.publish_update import assert_only_catalog_changes

    with pytest.raises(ValueError, match="allowlist"):
        assert_only_catalog_changes(["data/archive/notes.json"])


def test_archive_blob_must_be_valid_structured_json():
    from automation.publish_update import validate_archive_blob

    with pytest.raises(ValueError, match="valid JSON"):
        validate_archive_blob("not json")
    with pytest.raises(ValueError, match="JSON array"):
        validate_archive_blob("{}")


def test_candidate_import_check_rejects_unknown_schema_fields():
    import json
    from pathlib import Path
    from automation.publish_update import run_candidate_import_check

    catalog = records()
    catalog[0]["unexpected"] = "not supported by the importer"
    root = Path(__file__).resolve().parents[1]

    with pytest.raises(RuntimeError, match="Unknown event fields: unexpected"):
        run_candidate_import_check(root, json.dumps(catalog))


def test_quality_checks_migrate_local_schema_before_importing(tmp_path, monkeypatch):
    import subprocess
    from automation import publish_update

    calls = []

    def record(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(publish_update.subprocess, "run", record)
    publish_update.run_quality_checks(tmp_path)

    management_commands = [command[2] for command in calls if len(command) > 2 and command[1] == "manage.py"]
    assert management_commands[:3] == ["migrate", "import_events", "import_theater_deals"]


def test_publish_returns_no_change_without_committing_or_deploying(tmp_path):
    import json
    import subprocess
    from automation.publish_update import publish

    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.org"], cwd=tmp_path, check=True)
    (tmp_path / "data").mkdir()
    (tmp_path / "data/events.json").write_text(json.dumps(records()))
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout

    result = publish(tmp_path, now=datetime(2030, 5, 2, tzinfo=ZoneInfo("America/New_York")))

    after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True).stdout
    assert result == {
        "status": "no_change", "event_count": 12, "theater": {"status": "not_changed"},
    }
    assert after == before


def test_publish_validates_commits_pushes_and_confirms_changed_catalog(tmp_path):
    import json
    import subprocess
    from automation.publish_update import publish

    remote = tmp_path / "remote.git"
    root = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.org"], cwd=root, check=True)
    (root / "data").mkdir()
    catalog = records()
    (root / "data/events.json").write_text(json.dumps(catalog))
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=root, check=True)
    subprocess.run(["git", "push", "-qu", "origin", "main"], cwd=root, check=True)
    catalog[0]["title"] = "Updated title"
    (root / "data/events.json").write_text(json.dumps(catalog))
    checks = []

    def replace_index_after_validation():
        (root / "data/events.json").write_text("{}")
        subprocess.run(["git", "add", "data/events.json"], cwd=root, check=True)

    result = publish(
        root,
        now=datetime(2030, 5, 2, tzinfo=ZoneInfo("America/New_York")),
        quality_check=lambda path: checks.append(path),
        candidate_check=lambda path, blob: None,
        deployment_check=lambda revision, count: count == 12,
        before_commit=replace_index_after_validation,
    )

    local_revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    remote_revision = subprocess.run(
        ["git", "--git-dir", str(remote), "rev-parse", "main"], check=True, capture_output=True, text=True
    ).stdout.strip()
    remote_catalog = json.loads(subprocess.run(
        ["git", "--git-dir", str(remote), "show", "main:data/events.json"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout)
    assert result == {
        "status": "deployed", "event_count": 12, "revision": local_revision,
        # No theater seed in this fixture, so the honest answer is that theater did not ship.
        "theater": {"status": "not_changed"},
    }
    assert remote_revision == local_revision
    assert remote_catalog[0]["title"] == "Updated title"
    assert checks == [root]


def test_publish_revalidates_the_exact_catalog_staged_after_quality_checks(tmp_path):
    import json
    import subprocess
    from automation.publish_update import publish

    remote = tmp_path / "remote.git"
    root = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.org"], cwd=root, check=True)
    (root / "data").mkdir()
    catalog = records()
    (root / "data/events.json").write_text(json.dumps(catalog))
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=root, check=True)
    subprocess.run(["git", "push", "-qu", "origin", "main"], cwd=root, check=True)
    catalog[0]["title"] = "Candidate"
    (root / "data/events.json").write_text(json.dumps(catalog))

    def mutate_after_initial_validation(path):
        (path / "data/events.json").write_text("{}")

    with pytest.raises(ValueError, match="JSON array"):
        publish(
            root,
            now=datetime(2030, 5, 2, tzinfo=ZoneInfo("America/New_York")),
            quality_check=mutate_after_initial_validation,
            deployment_check=lambda revision, count: True,
        )

    assert subprocess.run(
        ["git", "log", "--format=%s", "-1"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip() == "initial"


def test_publish_restores_local_main_when_push_is_rejected(tmp_path):
    import json
    import subprocess
    from automation.publish_update import publish

    remote = tmp_path / "remote.git"
    root = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.org"], cwd=root, check=True)
    (root / "data").mkdir()
    catalog = records()
    (root / "data/events.json").write_text(json.dumps(catalog))
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)
    initial = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=root, check=True)
    subprocess.run(["git", "push", "-qu", "origin", "main"], cwd=root, check=True)
    hook = remote / "hooks/pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    catalog[0]["title"] = "Rejected update"
    (root / "data/events.json").write_text(json.dumps(catalog))

    with pytest.raises(RuntimeError, match="Git push failed"):
        publish(
            root,
            now=datetime(2030, 5, 2, tzinfo=ZoneInfo("America/New_York")),
            quality_check=lambda path: None,
            candidate_check=lambda path, blob: None,
            deployment_check=lambda revision, count: True,
        )

    assert subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip() == initial
    assert subprocess.run(
        ["git", "--git-dir", str(remote), "rev-parse", "main"], check=True, capture_output=True, text=True
    ).stdout.strip() == initial


def theater_records(*, verified_at="2030-04-01T12:00:00-04:00", count=1):
    return [
        {
            "title": f"Show {index}",
            "classification": "broadway",
            "official_url": f"https://example.org/show-{index}",
            "verified_at": verified_at,
            "offers": [
                {
                    "source_key": "rush",
                    "label": "Rush",
                    "price_label": "$40",
                    "price_min": 40,
                    "official_url": f"https://example.org/show-{index}/rush",
                    "eligible_until": "2030-06-01T23:59:59-04:00",
                }
            ],
        }
        for index in range(count)
    ]


def test_publish_still_ships_events_when_the_theater_seed_is_stale(tmp_path):
    import json
    import subprocess
    from automation.publish_update import publish

    remote = tmp_path / "remote.git"
    root = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.org"], cwd=root, check=True)
    (root / "data").mkdir()
    catalog = records()
    (root / "data/events.json").write_text(json.dumps(catalog))
    # Deliberately past the 7-day theater freshness gate.
    (root / "data/theater-deals.json").write_text(json.dumps(theater_records()))
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=root, check=True)
    subprocess.run(["git", "push", "-qu", "origin", "main"], cwd=root, check=True)
    catalog[0]["title"] = "Events still ship"
    (root / "data/events.json").write_text(json.dumps(catalog))

    result = publish(
        root,
        now=datetime(2030, 5, 2, tzinfo=ZoneInfo("America/New_York")),
        quality_check=lambda path: None,
        candidate_check=lambda path, blob: None,
        deployment_check=lambda revision, count: count == 12,
    )

    # Norman chose independent publication: a stale theater seed must not freeze the events site.
    assert result["status"] == "deployed"
    assert result["theater"]["status"] == "retained"
    pushed = subprocess.run(
        ["git", "--git-dir", str(remote), "show", "--name-only", "--format=", "main"],
        check=True, capture_output=True, text=True,
    ).stdout.split()
    assert "data/events.json" in pushed
    assert "data/theater-deals.json" not in pushed


def build_publishable_repo(tmp_path, *, theater_seed_text, add_theater=True):
    """A repo whose events catalog has a pending change and whose theater seed is as given."""
    import json
    import subprocess

    remote = tmp_path / "remote.git"
    root = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.org"], cwd=root, check=True)
    (root / "data").mkdir()
    catalog = records()
    (root / "data/events.json").write_text(json.dumps(catalog))
    if add_theater:
        (root / "data/theater-deals.json").write_text(theater_seed_text)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=root, check=True)
    subprocess.run(["git", "push", "-qu", "origin", "main"], cwd=root, check=True)
    catalog[0]["title"] = "Events still ship"
    (root / "data/events.json").write_text(json.dumps(catalog))
    return remote, root


def publish_here(root):
    from automation.publish_update import publish

    return publish(
        root,
        now=datetime(2030, 5, 2, tzinfo=ZoneInfo("America/New_York")),
        quality_check=lambda path: None,
        candidate_check=lambda path, blob: None,
        deployment_check=lambda revision, count: count == 12,
    )


@pytest.mark.parametrize(
    "seed",
    [
        "{not json",                              # unparseable
        '{"oops": 1}',                            # valid JSON, not an array
        "[42]",                                   # array of non-objects
        '[{"title": "no verified_at"}]',          # missing required field
        '[{"title": "bad ts", "classification": "broadway", "official_url": "https://example.org/x",'
        ' "verified_at": 1893456000, "offers": [{"source_key": "a", "label": "L", "price_label": "p",'
        ' "official_url": "https://example.org/x/a"}]}]',   # non-string timestamp
        "[" * 20000,                              # deeply nested: RecursionError, not ValueError
    ],
)
def test_publish_still_ships_events_when_the_theater_seed_is_malformed(tmp_path, seed):
    # Reading AND validating must be guarded: load_catalog raises for unparseable/non-array
    # seeds, and a non-string timestamp raises TypeError, neither of which is a ValueError.
    remote, root = build_publishable_repo(tmp_path, theater_seed_text=seed)

    result = publish_here(root)

    assert result["status"] == "deployed"
    assert result["theater"]["status"] == "retained"
    assert result["theater"]["reason"]
    pushed = subprocess_names(remote)
    assert "data/events.json" in pushed
    assert "data/theater-deals.json" not in pushed


def subprocess_names(remote):
    import subprocess

    return subprocess.run(
        ["git", "--git-dir", str(remote), "show", "--name-only", "--format=", "main"],
        check=True, capture_output=True, text=True,
    ).stdout.split()


def test_publish_reports_theater_not_changed_when_no_theater_seed_exists(tmp_path):
    remote, root = build_publishable_repo(tmp_path, theater_seed_text="[]", add_theater=False)

    result = publish_here(root)

    assert result["status"] == "deployed"
    # Reporting must reflect what shipped, not merely that validation did not raise.
    assert result["theater"] == {"status": "not_changed"}


def test_publish_reports_theater_updated_when_the_seed_actually_ships(tmp_path):
    import json

    remote, root = build_publishable_repo(
        tmp_path, theater_seed_text=json.dumps(theater_records(verified_at="2030-05-01T12:00:00-04:00"))
    )

    seed_path = root / "data/theater-deals.json"
    rows = json.loads(seed_path.read_text())
    rows[0]["title"] = "Renamed show"
    seed_path.write_text(json.dumps(rows))

    result = publish_here(root)

    assert result["status"] == "deployed"
    assert result["theater"] == {"status": "updated"}
    pushed = subprocess_names(remote)
    assert "data/events.json" in pushed
    assert "data/theater-deals.json" in pushed


def test_publish_still_ships_events_when_the_theater_seed_was_deleted(tmp_path):
    # A tracked-but-deleted seed stages a deletion, which would otherwise abort the whole publish.
    remote, root = build_publishable_repo(tmp_path, theater_seed_text="[]")
    (root / "data/theater-deals.json").unlink()

    result = publish_here(root)

    assert result["status"] == "deployed"
    assert result["theater"]["status"] == "retained"
    pushed = subprocess_names(remote)
    assert "data/events.json" in pushed
    assert "data/theater-deals.json" not in pushed


def test_publish_reports_theater_updated_for_an_archive_only_change(tmp_path):
    import json

    remote, root = build_publishable_repo(
        tmp_path, theater_seed_text=json.dumps(theater_records(verified_at="2030-05-01T12:00:00-04:00"))
    )
    archive_dir = root / "data/theater-archive"
    archive_dir.mkdir()
    (archive_dir / "2030-10.json").write_text(json.dumps([{
        "title": "Show 0", "classification": "broadway", "official_url": "https://example.org/show-0",
        "verified_at": "2030-05-01T12:00:00-04:00", "archive_reason": "expired",
        "archived_at": "2030-10-01T12:00:00-04:00",
        "offers": [{"source_key": "rush", "label": "Rush", "price_label": "$40",
                    "official_url": "https://example.org/show-0/rush"}],
    }]))

    result = publish_here(root)

    # Theater data shipped, so the flag must not say "not_changed" just because the seed itself
    # was untouched.
    assert result["status"] == "deployed"
    assert result["theater"] == {"status": "updated"}
    assert "data/theater-archive/2030-10.json" in subprocess_names(remote)


def test_publish_still_ships_events_when_the_theater_seed_is_staged_for_deletion(tmp_path):
    import subprocess

    # `git rm` strips the index entry entirely, so an index-only tracked check misses it and the
    # later `git add` fails with exit 128, aborting the events publish.
    remote, root = build_publishable_repo(tmp_path, theater_seed_text="[]")
    subprocess.run(["git", "rm", "-q", "data/theater-deals.json"], cwd=root, check=True)

    result = publish_here(root)

    assert result["status"] == "deployed"
    assert result["theater"]["status"] == "retained"
    assert "data/events.json" in subprocess_names(remote)


def test_publish_still_ships_events_when_a_theater_archive_is_malformed(tmp_path):
    remote, root = build_publishable_repo(tmp_path, theater_seed_text="[]")
    archive = root / "data/theater-archive"
    archive.mkdir()
    (archive / "2030-10.json").write_text("{not json")

    result = publish_here(root)

    assert result["status"] == "deployed"
    assert result["theater"]["status"] == "retained"
    assert "2030-10.json" in result["theater"]["reason"]


def test_publish_still_ships_events_with_a_prestaged_theater_path(tmp_path):
    import subprocess

    # Filtering a pre-staged theater path out of `paths` is not enough: it stays in the index and
    # trips the index-equality guard with a misleading "candidate Git index changed" error.
    remote, root = build_publishable_repo(tmp_path, theater_seed_text="[]")
    archive = root / "data/theater-archive"
    archive.mkdir()
    (archive / "2030-10.json").write_text("{not json")
    subprocess.run(["git", "add", "data/theater-archive/2030-10.json"], cwd=root, check=True)

    result = publish_here(root)

    assert result["status"] == "deployed"
    assert result["theater"]["status"] == "retained"
    assert "data/events.json" in subprocess_names(remote)


def valid_archive_blob():
    import json

    return json.dumps([{
        "title": "Show 0", "classification": "broadway", "official_url": "https://example.org/show-0",
        "verified_at": "2030-05-01T12:00:00-04:00", "archive_reason": "expired",
        "archived_at": "2030-10-01T12:00:00-04:00",
        "offers": [{"source_key": "rush", "label": "Rush", "price_label": "$40",
                    "official_url": "https://example.org/show-0/rush"}],
    }])


def test_a_theater_archive_mutated_mid_run_is_not_committed(tmp_path):
    import json
    import subprocess

    # The quality-check stage spans minutes, so the worktree can change after the pre-check.
    # The archive must therefore be validated from the tree that will actually be committed.
    remote, root = build_publishable_repo(
        tmp_path, theater_seed_text=json.dumps(theater_records(verified_at="2030-05-01T12:00:00-04:00"))
    )
    archive = root / "data/theater-archive"
    archive.mkdir()
    (archive / "2030-10.json").write_text(valid_archive_blob())
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "archive"], cwd=root, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=root, check=True)

    # A pending, valid archive edit puts the archive in the change set, so it is staged and would
    # be committed — then the quality-check stage corrupts the same file.
    pending = json.loads(valid_archive_blob())
    pending[0]["title"] = "Show 0 renamed"
    (archive / "2030-10.json").write_text(json.dumps(pending))
    events = json.loads((root / "data/events.json").read_text())
    events[0]["title"] = "Events still ship v2"
    (root / "data/events.json").write_text(json.dumps(events))

    def corrupt_the_archive(path):
        (path / "data/theater-archive/2030-10.json").write_text("{not json")

    from automation.publish_update import publish

    result = publish(
        root,
        now=datetime(2030, 5, 2, tzinfo=ZoneInfo("America/New_York")),
        quality_check=corrupt_the_archive,
        candidate_check=lambda path, blob: None,
        deployment_check=lambda revision, count: count == 12,
    )

    assert result["status"] == "deployed"
    assert result["theater"]["status"] == "retained"
    assert "2030-10.json" in result["theater"]["reason"]
    # The unverifiable bytes must not have reached the remote: the pushed archive is the
    # previously committed good copy, not the corrupt one.
    committed = subprocess.run(
        ["git", "--git-dir", str(remote), "show", "main:data/theater-archive/2030-10.json"],
        check=True, capture_output=True, text=True,
    ).stdout
    assert committed == valid_archive_blob()


def test_publish_leaves_the_index_alone_when_it_refuses(tmp_path):
    import json
    import subprocess

    # Theater problems now pull paths out of the index, but only after every hard guard, so a
    # refused run must not silently discard the operator's staged work.
    remote, root = build_publishable_repo(tmp_path, theater_seed_text="{not json")
    catalog = json.loads((root / "data/events.json").read_text())
    catalog[0]["title"] = "Also change events"
    (root / "data/events.json").write_text(json.dumps(catalog))
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "checkout", "-q", "-b", "feature"], cwd=root, check=True)
    staged_before = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.split()

    from automation.publish_update import publish

    with pytest.raises(ValueError, match="main branch"):
        publish(
            root,
            now=datetime(2030, 5, 2, tzinfo=ZoneInfo("America/New_York")),
            quality_check=lambda path: None,
            candidate_check=lambda path, blob: None,
            deployment_check=lambda revision, count: True,
        )

    staged_after = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.split()
    assert staged_after == staged_before


def test_publish_still_ships_events_when_a_theater_archive_is_staged_for_rename(tmp_path):
    import json
    import subprocess

    # Rename detection lists only the destination, leaving the source deletion behind to trip
    # the index-equality guard.
    remote, root = build_publishable_repo(tmp_path, theater_seed_text="{not json")
    archive = root / "data/theater-archive"
    archive.mkdir()
    (archive / "2030-09.json").write_text("[]")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "archive"], cwd=root, check=True)
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=root, check=True)
    # A pending events change is required, or the run correctly reports no_change.
    events = json.loads((root / "data/events.json").read_text())
    events[0]["title"] = "Events still ship v2"
    (root / "data/events.json").write_text(json.dumps(events))
    subprocess.run(["git", "mv", "data/theater-archive/2030-09.json",
                    "data/theater-archive/2030-08.json"], cwd=root, check=True)

    result = publish_here(root)

    assert result["status"] == "deployed"
    assert result["theater"]["status"] == "retained"
    assert "data/events.json" in subprocess_names(remote)


def test_cli_prints_machine_readable_result(monkeypatch, capsys):
    import json
    from automation import publish_update

    monkeypatch.setattr(
        publish_update,
        "publish",
        lambda root, now: {"status": "no_change", "event_count": 18},
    )

    assert publish_update.main([]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "no_change", "event_count": 18}
