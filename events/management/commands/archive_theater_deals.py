"""Archive expired theater offers without deleting their private history."""

import fcntl
import json
import os
import stat
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from events.catalog import write_json_atomic
from events.management.commands.archive_events import archive_lock_path

NYC = ZoneInfo("America/New_York")


def parse_aware(value):
    if not isinstance(value, str):
        raise ValueError("eligible_until must be an ISO timestamp")
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("eligible_until must include an explicit timezone offset")
    return result


def archive_key(record, offer):
    return record.get("official_url"), offer.get("source_key")


def merge_archive(existing, additions):
    merged, seen = [], set()
    for record in [*existing, *additions]:
        for offer in record.get("offers", []):
            key = archive_key(record, offer)
            if key in seen:
                continue
            seen.add(key)
            entry = deepcopy(record)
            entry["offers"] = [offer]
            merged.append(entry)
    return merged


def split_expired_offers(records, *, now):
    active, archived = [], []
    for record in records:
        current, expired = deepcopy(record), []
        offers = current.get("offers", [])
        live = []
        for offer in offers:
            deadline = offer.get("eligible_until")
            if deadline and parse_aware(deadline) <= now:
                expired.append(offer)
            else:
                live.append(offer)
        if live:
            current["offers"] = live
            active.append(current)
        if expired:
            item = deepcopy(record)
            item["offers"] = expired
            item["archive_reason"] = "expired"
            item["archived_at"] = now.astimezone(NYC).isoformat()
            archived.append(item)
    return active, archived


class Command(BaseCommand):
    help = "Move expired offers from the theater-deal seed to monthly private archives."

    def add_arguments(self, parser):
        parser.add_argument("--active-path", default=str(settings.BASE_DIR / "data/theater-deals.json"))
        parser.add_argument("--archive-dir", default=str(settings.BASE_DIR / "data/theater-archive"))
        parser.add_argument("--now", help="ISO timestamp for deterministic verification")

    def handle(self, *args, **options):
        active_path = Path(options["active_path"])
        archive_dir = Path(options["archive_dir"])
        try:
            now = parse_aware(options["now"]) if options.get("now") else datetime.now(NYC)
        except ValueError as exc:
            raise CommandError(f"Cannot archive theater deals: {exc}") from exc
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
            if not isinstance(records, list):
                raise ValueError("active seed must be a JSON array")
            active, expired = split_expired_offers(records, now=now)
        except (OSError, ValueError, TypeError) as exc:
            raise CommandError(f"Cannot archive theater deals: {exc}") from exc

        additions_by_month = {}
        for record in expired:
            for offer in record["offers"]:
                month = parse_aware(offer["eligible_until"]).astimezone(NYC).strftime("%Y-%m")
                item = deepcopy(record)
                item["offers"] = [offer]
                additions_by_month.setdefault(month, []).append(item)
        for month, additions in additions_by_month.items():
            target = archive_dir / f"{month}.json"
            existing = json.loads(target.read_text(encoding="utf-8")) if target.exists() else []
            if not isinstance(existing, list):
                raise CommandError(f"Cannot archive theater deals: {target} is not a JSON array")
            write_json_atomic(target, merge_archive(existing, additions))
        if expired:
            write_json_atomic(active_path, active)
        self.stdout.write(self.style.SUCCESS(f"Archived {sum(len(item['offers']) for item in expired)} expired theater offers."))
