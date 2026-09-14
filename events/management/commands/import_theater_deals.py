import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction
from django.utils.dateparse import parse_datetime
from django.utils.text import slugify

from events.models import TheaterDeal, TheaterOffer
from automation.theater_deals import MAX_THEATER_DEALS

DEAL_FIELDS = {
    "title", "slug", "classification", "venue", "neighborhood", "official_url", "image_url",
    "verified_at", "offers",
}
OFFER_FIELDS = {
    "source_key", "label", "price_label", "price_min", "price_max", "fees_included",
    "restrictions", "eligible_until", "official_url",
}


def aware_datetime(value, *, required=True):
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise ValueError("Dates must be ISO 8601 strings with an explicit timezone offset.")
    parsed = parse_datetime(value)
    if parsed is None or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Dates must be ISO 8601 strings with an explicit timezone offset.")
    return parsed


def decimal_or_none(value, field):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a number.")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field} must be a number.") from None


class Command(BaseCommand):
    help = "Import verified NYC theater deals by official show URL and stable offer source key. Never deletes records."

    def add_arguments(self, parser):
        parser.add_argument("path", nargs="?", default=str(settings.BASE_DIR / "data/theater-deals.json"))
        parser.add_argument("--sync", action="store_true", help="Deactivate deals and offers missing from this active seed.")

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            records = json.loads(Path(options["path"]).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CommandError(f"Cannot read theater deals JSON: {exc}") from exc
        if not isinstance(records, list):
            raise CommandError("Expected a JSON array of theater-deal objects.")
        if len(records) > MAX_THEATER_DEALS:
            raise CommandError(f"Theater-deal seed may contain at most {MAX_THEATER_DEALS} active shows.")

        active_urls, active_offer_ids = [], []
        for index, record in enumerate(records, 1):
            try:
                deal, offer_ids = self.import_record(record)
                active_urls.append(deal.official_url)
                active_offer_ids.extend(offer_ids)
            except (ValidationError, ValueError, TypeError, KeyError, IntegrityError) as exc:
                raise CommandError(f"Invalid theater-deal record {index}: {exc}") from exc

        if options["sync"]:
            TheaterDeal.objects.exclude(official_url__in=active_urls).update(is_active=False)
            TheaterDeal.objects.filter(official_url__in=active_urls).update(is_active=True)
            TheaterOffer.objects.exclude(pk__in=active_offer_ids).update(is_active=False)
            TheaterOffer.objects.filter(pk__in=active_offer_ids).update(is_active=True)
        self.stdout.write(self.style.SUCCESS(
            f"Imported {len(records)} theater deals. Existing sources updated; no records deleted."
        ))

    def import_record(self, record):
        if not isinstance(record, dict):
            raise ValueError("Expected a theater-deal object.")
        if unknown := set(record) - DEAL_FIELDS:
            raise ValueError(f"Unknown theater-deal fields: {', '.join(sorted(unknown))}")
        fields = dict(record)
        offers = fields.pop("offers")
        if not isinstance(offers, list) or not offers:
            raise ValueError("Every theater deal must contain at least one offer.")
        if fields.get("image_url") is None:
            fields["image_url"] = ""
        fields["verified_at"] = aware_datetime(fields.get("verified_at"))
        url = fields.pop("official_url")
        if not isinstance(url, str):
            raise ValueError("official_url must be a URL string.")
        deal = TheaterDeal.objects.filter(official_url=url).first()
        if deal is None:
            slug = fields.pop("slug", "") or slugify(fields["title"])
            suffix = hashlib.sha256(url.encode()).hexdigest()[:12]
            deal = TheaterDeal(official_url=url, slug=f"{slug[:230] or 'theater-deal'}-{suffix}")
        else:
            fields.pop("slug", None)
        for key, value in fields.items():
            setattr(deal, key, value)
        deal.is_active = True
        deal.full_clean()
        deal.save()

        offer_ids = []
        for item in offers:
            if not isinstance(item, dict) or set(item) - OFFER_FIELDS:
                raise ValueError("Each theater offer may contain only known fields.")
            offer_fields = dict(item)
            offer_fields["eligible_until"] = aware_datetime(
                offer_fields.get("eligible_until"), required=False
            )
            for field in ("price_min", "price_max"):
                offer_fields[field] = decimal_or_none(offer_fields.get(field), field)
            if offer_fields.get("fees_included") is not None and not isinstance(offer_fields["fees_included"], bool):
                raise ValueError("fees_included must be a JSON boolean.")
            key = offer_fields.pop("source_key")
            offer = TheaterOffer.objects.filter(deal=deal, source_key=key).first()
            if offer is None:
                offer = TheaterOffer(deal=deal, source_key=key)
            for field, value in offer_fields.items():
                setattr(offer, field, value)
            offer.is_active = True
            offer.full_clean()
            offer.save()
            offer_ids.append(offer.pk)
        return deal, offer_ids
