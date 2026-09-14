import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

from automation.theater_deals import validate_theater_deals

MIN_EVENTS = 12
TARGET_EVENTS = 20
MAX_EVENTS = 24
# The headline section is a curation, not a slice of the feed; cap it explicitly.
MAX_TOP_PICKS = 3
VERIFICATION_MAX_AGE_DAYS = 7
NYC = ZoneInfo("America/New_York")
DEFAULT_HEALTH_URL = "https://events-webpage-gamma.vercel.app/health/"
POLL_SECONDS = 90


def _aware(value):
    if not isinstance(value, str):
        raise ValueError("Each occurrence must use aware ISO timestamps.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("Each occurrence must use aware ISO timestamps.") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Each occurrence must use aware ISO timestamps.")
    return parsed


def changed_paths(root):
    commands = (
        ["git", "diff", "--name-only", "-z"],
        ["git", "diff", "--cached", "--name-only", "-z"],
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
    )
    paths = set()
    for command in commands:
        output = subprocess.run(command, cwd=root, check=True, capture_output=True).stdout
        paths.update(part.decode("utf-8") for part in output.split(b"\0") if part)
    return sorted(paths)


def assert_only_catalog_changes(paths):
    for value in paths:
        path = PurePosixPath(value)
        allowed = path in {
            PurePosixPath("data/events.json"),
            PurePosixPath("data/theater-deals.json"),
        } or bool(
            path.parent in {PurePosixPath("data/archive"), PurePosixPath("data/theater-archive")}
            and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])\.json", path.name)
        )
        if ".." in path.parts or not allowed:
            raise ValueError(f"Change outside the catalog allowlist: {value}")


def is_theater_path(value):
    """True for the theater seed and its monthly archives, which publish independently."""
    path = PurePosixPath(value)
    return path == PurePosixPath("data/theater-deals.json") or path.parent == PurePosixPath(
        "data/theater-archive"
    )


def deployment_matches(payload, *, revision, minimum_count):
    return (
        isinstance(payload, dict)
        and payload.get("status") == "ok"
        and payload.get("revision") == revision
        and isinstance(payload.get("upcoming_occurrences"), int)
        and payload["upcoming_occurrences"] >= minimum_count
    )


def load_catalog(path, *, label="active event seed"):
    try:
        records = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        raise ValueError(f"Cannot read the {label}: {exc}") from exc
    if not isinstance(records, list):
        raise ValueError(f"The {label} must be a JSON array.")
    return records


def validate_archive_blob(blob):
    try:
        records = json.loads(blob)
    except (TypeError, ValueError) as exc:
        raise ValueError("Archive file must contain valid JSON.") from exc
    if not isinstance(records, list):
        raise ValueError("Archive file must be a JSON array.")
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("occurrences"), list):
            raise ValueError("Every archive record must contain an occurrences array.")
        if not record["occurrences"]:
            raise ValueError("Every archive record must contain an archived occurrence.")
        if not isinstance(record.get("archive_reason"), str) or not record["archive_reason"]:
            raise ValueError("Every archive record must include an archive reason.")
        _aware(record.get("archived_at"))
        for occurrence in record["occurrences"]:
            if not isinstance(occurrence, dict) or not occurrence.get("source_key"):
                raise ValueError("Every archived occurrence must include a source key.")
            _aware(occurrence.get("starts_at"))
            if occurrence.get("ends_at"):
                _aware(occurrence["ends_at"])
    return records


def validate_theater_archive_blob(blob):
    try:
        records = json.loads(blob)
    except (TypeError, ValueError) as exc:
        raise ValueError("Theater archive file must contain valid JSON.") from exc
    if not isinstance(records, list):
        raise ValueError("Theater archive file must be a JSON array.")
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("offers"), list) or not record["offers"]:
            raise ValueError("Every theater archive record must contain offers.")
        if not isinstance(record.get("archive_reason"), str) or not record["archive_reason"]:
            raise ValueError("Every theater archive record must include an archive reason.")
        _aware(record.get("archived_at"))
        for offer in record["offers"]:
            if not isinstance(offer, dict) or not offer.get("source_key"):
                raise ValueError("Every archived theater offer must include a source key.")
    return records


def _git(root, *args, check=True):
    return subprocess.run(
        ["git", *args], cwd=root, check=check, capture_output=True, text=True
    )


def _staged_paths(root):
    output = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    return sorted(part.decode("utf-8") for part in output.split(b"\0") if part)


def _tree_paths(root, base, tree):
    output = subprocess.run(
        ["git", "diff", "--name-only", "-z", base, tree],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    return sorted(part.decode("utf-8") for part in output.split(b"\0") if part)


def _tree_blob(root, tree, path):
    result = _git(root, "show", f"{tree}:{path}", check=False)
    if result.returncode:
        raise ValueError(f"Catalog history may not be deleted: {path}")
    return result.stdout


def run_quality_checks(root):
    python = root / ".venv/bin/python"
    if not python.exists():
        python = Path(sys.executable)
    env = os.environ.copy()
    env["DEBUG"] = "1"
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    for key in ("DATABASE_URL", "SECRET_KEY", "ALLOWED_HOSTS", "CSRF_TRUSTED_ORIGINS", "VERCEL", "VERCEL_ENV"):
        env.pop(key, None)
    commands = [
        [str(python), "manage.py", "migrate", "--noinput"],
        [str(python), "manage.py", "import_events", "--sync"],
        [str(python), "manage.py", "import_theater_deals", "--sync"],
        [str(python), "-m", "pytest", "-p", "django", "-q"],
        [str(python), "manage.py", "check"],
        [str(python), "manage.py", "makemigrations", "--check", "--dry-run"],
    ]
    for command in commands:
        completed = subprocess.run(
            command, cwd=root, env=env, check=False, capture_output=True, text=True
        )
        if completed.returncode:
            detail = (completed.stderr or completed.stdout).strip().splitlines()
            raise RuntimeError(
                f"Quality check failed ({' '.join(command)}): "
                + (detail[-1] if detail else f"exit {completed.returncode}")
            )


def run_candidate_import_check(root, catalog_blob, theater_blob=None):
    python = root / ".venv/bin/python"
    if not python.exists():
        python = Path(sys.executable)
    env = os.environ.copy()
    env["DEBUG"] = "1"
    for key in ("DATABASE_URL", "SECRET_KEY", "ALLOWED_HOSTS", "CSRF_TRUSTED_ORIGINS", "VERCEL", "VERCEL_ENV"):
        env.pop(key, None)
    with tempfile.TemporaryDirectory() as directory:
        event_path = Path(directory) / "events.json"
        event_path.write_text(catalog_blob, encoding="utf-8")
        commands = [[str(python), "manage.py", "import_events", str(event_path), "--sync"]]
        if theater_blob is not None:
            theater_path = Path(directory) / "theater-deals.json"
            theater_path.write_text(theater_blob, encoding="utf-8")
            commands.append([str(python), "manage.py", "import_theater_deals", str(theater_path), "--sync"])
        for command in commands:
            completed = subprocess.run(command, cwd=root, env=env, check=False, capture_output=True, text=True)
            if completed.returncode:
                detail = (completed.stderr or completed.stdout).strip().splitlines()
                raise RuntimeError(
                    "Candidate catalog failed importer validation: "
                    + (detail[-1] if detail else f"exit {completed.returncode}")
                )


def _reason(exc):
    return f"{type(exc).__name__}: {exc}"


def theater_archive_problem(root):
    """A malformed theater archive retains the theater page rather than blocking events.

    Theater archives are inert data that the app never renders, so a bad one is a theater-side
    problem. Events archives stay hard-fail-closed elsewhere.
    """
    directory = Path(root) / "data/theater-archive"
    if not directory.is_dir():
        return None
    for path in sorted(directory.glob("*.json")):
        try:
            validate_theater_archive_blob(path.read_text(encoding="utf-8"))
        except Exception as exc:
            return f"{path.name}: {_reason(exc)}"
    return None


def read_theater_problem(root, *, now):
    """Reason the theater seed cannot ship, or None when it is absent or valid.

    This guard must never abort the events publish, so it catches broadly and treats a deleted
    seed as a problem too: reading and validation share one block, a deeply nested seed raises
    RecursionError rather than ValueError, and a staged deletion leaves no index entry to detect.
    A broad catch costs at most a retained theater page, which the result reports explicitly.
    """
    root = Path(root)
    path = root / "data/theater-deals.json"
    if not path.exists():
        # A staged deletion (git rm) leaves nothing in the index, so HEAD must be consulted too.
        for probe in (["ls-files", "--error-unmatch", "--", "data/theater-deals.json"],
                      ["cat-file", "-e", "HEAD:data/theater-deals.json"]):
            if _git(root, *probe, check=False).returncode == 0:
                return "The theater-deals seed is tracked but missing from the working tree."
        return None
    try:
        validate_theater_deals(load_catalog(path, label="theater-deals seed"), now=now)
    except Exception as exc:
        return _reason(exc)
    return theater_archive_problem(root)


def theater_result(problem, *, changed):
    if problem:
        return {"status": "retained", "reason": problem}
    return {"status": "updated" if changed else "not_changed"}


def fetch_health(url, *, timeout=10):
    request = urllib.request.Request(
        url,
        headers={"Cache-Control": "no-cache", "User-Agent": "normans-events-updater"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def poll_deployment(revision, *, minimum_count, url=DEFAULT_HEALTH_URL, seconds=POLL_SECONDS):
    deadline = time.monotonic() + seconds
    while True:
        try:
            if deployment_matches(fetch_health(url), revision=revision, minimum_count=minimum_count):
                return True
        except (urllib.error.URLError, ValueError, OSError, TimeoutError):
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(5)


def publish(
    root,
    *,
    now,
    quality_check=None,
    candidate_check=None,
    deployment_check=None,
    before_commit=None,
):
    root = Path(root)
    records = load_catalog(root / "data/events.json")
    validate_catalog(records, now=now)
    # Theater deals publish independently of the events feed: any problem reading or validating
    # the theater seed loses only the theater changes, so the last verified deals stay live and
    # the events site keeps updating. The events catalog itself stays fail-closed — a broken
    # events seed still publishes nothing.
    theater_problem = read_theater_problem(root, now=now)
    paths = changed_paths(root)
    if theater_problem:
        paths = [path for path in paths if not is_theater_path(path)]
        # A pre-staged theater path would otherwise trip the index-equality guard below and
        # abort the events publish with an "index changed" message unrelated to the real cause.
        pre_staged = [path for path in _staged_paths(root) if is_theater_path(path)]
        if pre_staged:
            _git(root, "reset", "-q", "--", *pre_staged)
    if not paths:
        result = {"status": "no_change", "event_count": len(records)}
        result["theater"] = theater_result(theater_problem, changed=False)
        return result
    assert_only_catalog_changes(paths)
    if _git(root, "branch", "--show-current").stdout.strip() != "main":
        raise ValueError("Automated publishing is allowed only from the main branch.")
    _git(root, "fetch", "origin", "main")
    local_before = _git(root, "rev-parse", "HEAD").stdout.strip()
    remote_before = _git(root, "rev-parse", "origin/main").stdout.strip()
    if local_before != remote_before:
        raise ValueError("Local main is not synchronized with origin/main; refusing to publish.")

    (quality_check or run_quality_checks)(root)
    _git(root, "add", "--", *paths)
    staged_paths = _staged_paths(root)
    if not staged_paths:
        result = {"status": "no_change", "event_count": len(records)}
        result["theater"] = theater_result(theater_problem, changed=False)
        return result
    if set(staged_paths) != set(paths):
        raise RuntimeError("The candidate Git index changed during publication; refusing to commit.")
    assert_only_catalog_changes(staged_paths)
    candidate_tree = _git(root, "write-tree").stdout.strip()
    candidate_paths = _tree_paths(root, local_before, candidate_tree)
    if candidate_paths != staged_paths:
        raise RuntimeError("The candidate Git tree does not match the reviewed catalog paths.")
    catalog_blob = _tree_blob(root, candidate_tree, "data/events.json")
    try:
        staged_records = json.loads(catalog_blob)
    except ValueError as exc:
        raise ValueError("Active event seed must contain valid JSON.") from exc
    if not isinstance(staged_records, list):
        raise ValueError("Active event seed must be a JSON array.")
    validate_catalog(staged_records, now=now)
    theater_blob = None
    if "data/theater-deals.json" in candidate_paths:
        theater_blob = _tree_blob(root, candidate_tree, "data/theater-deals.json")
        try:
            staged_theater_records = json.loads(theater_blob)
        except ValueError as exc:
            raise ValueError("Active theater-deals seed must contain valid JSON.") from exc
        validate_theater_deals(staged_theater_records, now=now)
    if candidate_check:
        candidate_check(root, catalog_blob)
    else:
        run_candidate_import_check(root, catalog_blob, theater_blob)
    for path in candidate_paths:
        if path.startswith("data/archive/"):
            validate_archive_blob(_tree_blob(root, candidate_tree, path))
        # Theater archives are validated up front in read_theater_problem, where a bad one is a
        # theater-side problem rather than a reason to withhold the events publish.

    if before_commit:
        before_commit()
    message = f"chore(events): refresh NYC catalog {now.astimezone(NYC):%Y-%m-%d}"
    revision = _git(
        root, "commit-tree", candidate_tree, "-p", local_before, "-m", message
    ).stdout.strip()
    _git(root, "update-ref", "refs/heads/main", revision, local_before)
    committed_tree = _git(root, "rev-parse", f"{revision}^{{tree}}").stdout.strip()
    if committed_tree != candidate_tree:
        raise RuntimeError("Created commit does not match the validated catalog tree.")
    pushed = _git(root, "push", "origin", f"{revision}:refs/heads/main", check=False)
    if pushed.returncode:
        remote = _git(root, "ls-remote", "origin", "refs/heads/main", check=False)
        remote_revision = remote.stdout.split()[0] if remote.returncode == 0 and remote.stdout.split() else None
        if remote_revision != revision:
            _git(root, "update-ref", "refs/heads/main", local_before, revision)
            raise RuntimeError(f"Git push failed: {(pushed.stderr or pushed.stdout).strip()}")
    remote_revision = _git(root, "ls-remote", "origin", "refs/heads/main").stdout.split()[0]
    if remote_revision != revision:
        raise RuntimeError("Git push returned without updating origin/main to the new revision.")

    verifier = deployment_check or (
        lambda commit, count: poll_deployment(commit, minimum_count=MIN_EVENTS)
    )
    status = "deployed" if verifier(revision, len(staged_records)) else "deployment_unconfirmed"
    result = {"status": status, "event_count": len(staged_records), "revision": revision}
    # Report off what actually shipped, not off "validation happened not to fail". Any theater
    # path counts, so an archive-only push is not misreported as unchanged.
    result["theater"] = theater_result(
        theater_problem, changed=any(is_theater_path(path) for path in candidate_paths)
    )
    return result


def validate_catalog(records, *, now):
    if not isinstance(records, list) or not MIN_EVENTS <= len(records) <= MAX_EVENTS:
        raise ValueError(f"Catalog must contain {MIN_EVENTS} to {MAX_EVENTS} events.")
    urls = [record.get("official_url") for record in records]
    if any(not isinstance(url, str) or not url.startswith("https://") for url in urls) or len(set(urls)) != len(urls):
        raise ValueError("Every event must have a unique HTTPS official source URL.")
    # Require a real boolean, matching the importer, instead of accepting any truthy value.
    for record in records:
        if "top_pick" in record and not isinstance(record["top_pick"], bool):
            raise ValueError("top_pick must be a JSON boolean.")
    top_picks = [record for record in records if record.get("top_pick")]
    if len(top_picks) > MAX_TOP_PICKS:
        raise ValueError(
            f"At most {MAX_TOP_PICKS} events may be flagged top_pick; found {len(top_picks)}."
        )
    horizon = now + timedelta(days=30)
    for record in records:
        verified_at = _aware(record.get("verified_at"))
        if verified_at > now or verified_at < now - timedelta(days=VERIFICATION_MAX_AGE_DAYS):
            raise ValueError(
                f"Every event verified_at must be within the previous {VERIFICATION_MAX_AGE_DAYS} days."
            )
        occurrences = record.get("occurrences")
        if not isinstance(occurrences, list) or not occurrences:
            raise ValueError("Every event must have at least one occurrence.")
        for occurrence in occurrences:
            starts_at = _aware(occurrence.get("starts_at"))
            ends_at = _aware(occurrence["ends_at"]) if occurrence.get("ends_at") else None
            is_past = ends_at <= now if ends_at else starts_at.astimezone(NYC).date() < now.astimezone(NYC).date()
            if is_past or starts_at > horizon:
                raise ValueError("Every occurrence must be active and within the next 30 days.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate and publish Norman's active NYC event catalog.")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    options = parser.parse_args(argv)
    try:
        result = publish(Path(options.root), now=datetime.now(NYC))
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}))
        return 1
    print(json.dumps(result))
    return 2 if result["status"] == "deployment_unconfirmed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
