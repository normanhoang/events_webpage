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


def test_validate_catalog_accepts_twelve_to_twenty_current_verified_events():
    from automation.publish_update import validate_catalog

    now = datetime(2030, 5, 2, tzinfo=ZoneInfo("America/New_York"))
    validate_catalog(records(12), now=now)
    validate_catalog(records(20), now=now)

    with pytest.raises(ValueError, match="12 to 20"):
        validate_catalog(records(11), now=now)
    with pytest.raises(ValueError, match="12 to 20"):
        validate_catalog(records(21), now=now)


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

    assert_only_catalog_changes(["data/events.json", "data/archive/2030-05.json"])
    for path in ["README.md", "events/models.py", "data/archive/../events.json"]:
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
    assert result == {"status": "no_change", "event_count": 12}
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
    assert result == {"status": "deployed", "event_count": 12, "revision": local_revision}
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
