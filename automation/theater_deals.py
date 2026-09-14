"""Validation helpers for the independent NYC theater-deals seed."""

from datetime import datetime, timedelta

MAX_THEATER_DEALS = 50
VERIFICATION_MAX_AGE_DAYS = 7


def validate_theater_deals(records, *, now):
    if not isinstance(records, list) or len(records) > MAX_THEATER_DEALS:
        raise ValueError(f"Theater-deals catalog must contain at most {MAX_THEATER_DEALS} shows.")
    urls = [record.get("official_url") for record in records]
    if any(not isinstance(url, str) or not url.startswith("https://") for url in urls) or len(set(urls)) != len(urls):
        raise ValueError("Every theater deal must have a unique HTTPS official source URL.")
    seen_offers = set()
    for record in records:
        verified = datetime.fromisoformat(record.get("verified_at", ""))
        if verified.tzinfo is None or verified.utcoffset() is None:
            raise ValueError("Every theater deal verified_at must use an aware ISO timestamp.")
        if verified > now or verified < now - timedelta(days=VERIFICATION_MAX_AGE_DAYS):
            raise ValueError("Every theater deal verified_at must be recent and non-future.")
        if record.get("classification") not in {"broadway", "off_broadway", "other"}:
            raise ValueError("Every theater deal must use a supported classification.")
        offers = record.get("offers")
        if not isinstance(offers, list) or not offers:
            raise ValueError("Every theater deal must include at least one offer.")
        for offer in offers:
            key = (record["official_url"], offer.get("source_key"))
            if not offer.get("source_key") or key in seen_offers:
                raise ValueError("Every theater offer must have a unique source key per show.")
            seen_offers.add(key)
            if not isinstance(offer.get("official_url"), str) or not offer["official_url"].startswith("https://"):
                raise ValueError("Every theater offer must have an HTTPS source URL.")
            deadline = offer.get("eligible_until")
            if deadline:
                until = datetime.fromisoformat(deadline)
                if until.tzinfo is None or until.utcoffset() is None:
                    raise ValueError("Every theater offer deadline must use an aware ISO timestamp.")
                if until <= now:
                    raise ValueError("Expired theater offers must be archived before publication.")
