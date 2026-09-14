import fcntl
import hashlib
import json
import os
import stat
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from events.catalog import archive_past_records, merge_archive, write_json_atomic

NYC = ZoneInfo("America/New_York")


def archive_lock_path(active_path):
    base = Path(os.environ.get("XDG_RUNTIME_DIR", Path.home() / ".cache"))
    private = base / "normans-events"
    private.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = private.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        raise OSError("Archive lock directory is not private and owned by the current user.")
    lock_name = hashlib.sha256(str(Path(active_path).resolve()).encode()).hexdigest()[:20]
    return private / f"archive-{lock_name}.lock"


class Command(BaseCommand):
    help = "Move expired seed occurrences into an idempotent monthly archive."

    def add_arguments(self, parser):
        parser.add_argument("--active", default=str(settings.BASE_DIR / "data/events.json"))
        parser.add_argument("--archive-dir", default=str(settings.BASE_DIR / "data/archive"))
        parser.add_argument("--now", help="Aware ISO timestamp for deterministic runs and tests.")

    def handle(self, *args, **options):
        active_path = Path(options["active"])
        archive_dir = Path(options["archive_dir"])
        now = parse_datetime(options["now"]) if options.get("now") else timezone.now()
        if now is None or timezone.is_naive(now):
            raise CommandError("--now must be an ISO timestamp with an explicit timezone offset.")
        lock_path = archive_lock_path(active_path)
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as lock:
            metadata = os.fstat(lock.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
                raise OSError("Archive lock is not a regular file owned by the current user.")
            fcntl.flock(lock, fcntl.LOCK_EX)
            self._archive(active_path, archive_dir, now)

    def _archive(self, active_path, archive_dir, now):
        try:
            records = json.loads(active_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CommandError(f"Cannot read active event seed: {exc}") from exc
        if not isinstance(records, list):
            raise CommandError("Active event seed must be a JSON array.")

        active, archived = archive_past_records(records, now=now)
        if not archived:
            self.stdout.write("No expired occurrences to archive.")
            return

        archive_path = archive_dir / f"{now.astimezone(NYC):%Y-%m}.json"
        if archive_path.exists():
            try:
                existing = json.loads(archive_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise CommandError(f"Cannot read archive file: {exc}") from exc
            if not isinstance(existing, list):
                raise CommandError("Archive file must be a JSON array.")
        else:
            existing = []

        # Save history first: if the active replacement fails, the next run's
        # idempotent merge removes the duplicate instead of losing history.
        write_json_atomic(archive_path, merge_archive(existing, archived))
        write_json_atomic(active_path, active)
        occurrence_count = sum(len(item["occurrences"]) for item in archived)
        self.stdout.write(self.style.SUCCESS(
            f"Archived {occurrence_count} expired occurrence(s) to {archive_path}."
        ))
