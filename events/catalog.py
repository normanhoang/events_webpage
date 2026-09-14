import json
import os
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from zoneinfo import ZoneInfo

NYC = ZoneInfo("America/New_York")


def write_json_atomic(path, records):
    """Replace a JSON file in one step so a crash cannot leave a partial catalog."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(records, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def merge_archive(existing, additions):
    """Deduplicate archive rows by (official_url, source_key) so reruns stay idempotent."""
    merged = []
    seen = set()
    for record in [*existing, *additions]:
        for occurrence in record.get("occurrences", []):
            key = (record.get("official_url"), occurrence.get("source_key"))
            if key in seen:
                continue
            seen.add(key)
            item = dict(record)
            item["occurrences"] = [occurrence]
            merged.append(item)
    return merged


def _parse_aware(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Occurrence times must include an explicit timezone offset.")
    return parsed


def _is_past(occurrence, now):
    if occurrence.get("ends_at"):
        return _parse_aware(occurrence["ends_at"]) <= now
    return _parse_aware(occurrence["starts_at"]).astimezone(NYC).date() < now.astimezone(NYC).date()


def archive_past_records(records, *, now):
    """Split expired occurrences from an active event seed without deleting history."""
    active_records = []
    archived_records = []
    for record in records:
        current = deepcopy(record)
        past = []
        future = []
        for occurrence in current.get("occurrences", []):
            (past if _is_past(occurrence, now) else future).append(occurrence)
        if future:
            current["occurrences"] = future
            active_records.append(current)
        if past:
            archived = deepcopy(record)
            archived["occurrences"] = past
            archived["archive_reason"] = "past"
            archived["archived_at"] = now.astimezone(NYC).isoformat()
            archived_records.append(archived)
    return active_records, archived_records
