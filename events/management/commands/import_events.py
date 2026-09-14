import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.text import slugify

from events.models import Event, Occurrence

EVENT_FIELDS = {
    "title", "slug", "description", "category", "tags", "venue", "neighborhood", "borough",
    "official_url", "image_url", "price_label", "price_min", "price_max", "currency",
    "fit_reason", "verified_at", "top_pick", "occurrences",
}
OCCURRENCE_FIELDS = {"source_key", "starts_at", "ends_at"}


def aware_datetime(value):
    if not isinstance(value, str):
        raise ValueError("Dates must be ISO 8601 strings with an explicit timezone offset.")
    parsed = parse_datetime(value)
    if parsed is None or timezone.is_naive(parsed):
        raise ValueError("Dates must be ISO 8601 strings with an explicit timezone offset.")
    return parsed


class Command(BaseCommand):
    help = "Import editorial events by official URL and stable occurrence source_key. Never deletes records."

    def add_arguments(self, parser):
        parser.add_argument("path", nargs="?", default=str(settings.BASE_DIR / "data/events.json"))
        parser.add_argument("--sync", action="store_true", help="Deactivate records missing from this active seed.")

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            records = json.loads(Path(options["path"]).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CommandError(f"Cannot read events JSON: {exc}") from exc
        if not isinstance(records, list):
            raise CommandError("Expected a JSON array of event objects.")
        active_urls = []
        active_occurrence_ids = []
        for index, record in enumerate(records, 1):
            try:
                event, occurrence_ids = self.import_record(record)
                active_urls.append(event.official_url)
                active_occurrence_ids.extend(occurrence_ids)
            except (ValidationError, ValueError, TypeError, KeyError, IntegrityError) as exc:
                raise CommandError(f"Invalid record {index}: {exc}") from exc
        if options["sync"]:
            Event.objects.exclude(official_url__in=active_urls).update(is_active=False)
            Event.objects.filter(official_url__in=active_urls).update(is_active=True)
            Occurrence.objects.exclude(pk__in=active_occurrence_ids).update(is_active=False)
            Occurrence.objects.filter(pk__in=active_occurrence_ids).update(is_active=True)
        self.stdout.write(self.style.SUCCESS(f"Imported {len(records)} events. Existing sources updated; no records deleted."))

    def import_record(self, record):
        if not isinstance(record, dict):
            raise ValueError("Expected an event object.")
        if unknown := set(record) - EVENT_FIELDS:
            raise ValueError(f"Unknown event fields: {', '.join(sorted(unknown))}")
        fields = dict(record)
        dates = fields.pop("occurrences")
        if not isinstance(dates, list):
            raise ValueError("occurrences must be an array.")
        if fields.get("image_url") is None:
            fields["image_url"] = ""
        if "top_pick" in fields and not isinstance(fields["top_pick"], bool):
            raise ValueError("top_pick must be a JSON boolean.")
        for price_field in ("price_min", "price_max"):
            if fields.get(price_field) is not None:
                if isinstance(fields[price_field], bool):
                    raise ValueError(f"{price_field} must be a number.")
                try:
                    fields[price_field] = Decimal(str(fields[price_field]))
                except (InvalidOperation, ValueError):
                    raise ValueError(f"{price_field} must be a number.") from None
        if fields.get("verified_at") is not None:
            fields["verified_at"] = aware_datetime(fields["verified_at"])
        url = fields.pop("official_url")
        if not isinstance(url, str):
            raise ValueError("official_url must be a URL string.")
        event = Event.objects.filter(official_url=url).first()
        if event is None:
            suffix = hashlib.sha256(url.encode()).hexdigest()[:12]
            event = Event(official_url=url, slug=f"{slugify(fields['title'])[:230] or 'event'}-{suffix}")
        for key, value in fields.items():
            setattr(event, key, value)
        event.is_active = True
        event.full_clean()
        event.save()
        occurrence_ids = []
        for date in dates:
            if not isinstance(date, dict) or set(date) - OCCURRENCE_FIELDS:
                raise ValueError("Each occurrence must contain only source_key, starts_at, and ends_at.")
            key = date["source_key"]
            occurrence = Occurrence.objects.filter(event=event, source_key=key).first() or Occurrence(event=event, source_key=key)
            occurrence.starts_at = aware_datetime(date["starts_at"])
            occurrence.ends_at = aware_datetime(date["ends_at"]) if date.get("ends_at") is not None else None
            occurrence.is_active = True
            occurrence.full_clean()
            occurrence.save()
            occurrence_ids.append(occurrence.pk)
        return event, occurrence_ids
