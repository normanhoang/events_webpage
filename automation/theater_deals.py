"""Validation helpers for the independent NYC theater-deals seed."""

from datetime import datetime, timedelta

MAX_THEATER_DEALS = 50
VERIFICATION_MAX_AGE_DAYS = 7
CLASSIFICATIONS = {"broadway", "off_broadway", "other"}


def aware_iso(value, field):
    """Parse an ISO timestamp, converting every malformed shape into ValueError.

    str/None/dict timestamps make ``datetime.fromisoformat`` raise TypeError, which would
    otherwise escape the publisher's ValueError handling and abort the events publish.
    """
    if not isinstance(value, str):
        raise ValueError(f"Every theater deal {field} must be an ISO 8601 string.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"Every theater deal {field} must be an ISO 8601 string.") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"Every theater deal {field} must use an aware ISO timestamp.")
    return parsed


def validate_theater_deals(records, *, now):
    if not isinstance(records, list):
        raise ValueError("Theater-deals catalog must be a JSON array.")
    if len(records) > MAX_THEATER_DEALS:
        raise ValueError(f"Theater-deals catalog must contain at most {MAX_THEATER_DEALS} shows.")

    # Type-check every record before touching attributes: a bare ``record.get`` on a non-dict
    # raises AttributeError, which would bypass the publisher's ValueError handling entirely.
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Every theater deal must be a JSON object.")

    urls = [record.get("official_url") for record in records]
    if any(not isinstance(url, str) or not url.startswith("https://") for url in urls) or len(set(urls)) != len(urls):
        raise ValueError("Every theater deal must have a unique HTTPS official source URL.")

    # image_url is optional but lands in a template src attribute, so only https may pass. A
    # non-string must raise ValueError too, because that is the only type the publisher catches.
    for record in records:
        image = record.get("image_url")
        if image and (not isinstance(image, str) or not image.startswith("https://")):
            raise ValueError("Every theater deal image_url must be an HTTPS URL when present.")

    seen_offers = set()
    for record in records:
        verified = aware_iso(record.get("verified_at"), "verified_at")
        if verified > now or verified < now - timedelta(days=VERIFICATION_MAX_AGE_DAYS):
            raise ValueError("Every theater deal verified_at must be recent and non-future.")
        if record.get("classification") not in CLASSIFICATIONS:
            raise ValueError("Every theater deal must use a supported classification.")
        offers = record.get("offers")
        if not isinstance(offers, list) or not offers:
            raise ValueError("Every theater deal must include at least one offer.")
        for offer in offers:
            if not isinstance(offer, dict):
                raise ValueError("Every theater offer must be a JSON object.")
            key = (record["official_url"], offer.get("source_key"))
            if not offer.get("source_key") or key in seen_offers:
                raise ValueError("Every theater offer must have a unique source key per show.")
            seen_offers.add(key)
            if not isinstance(offer.get("official_url"), str) or not offer["official_url"].startswith("https://"):
                raise ValueError("Every theater offer must have an HTTPS source URL.")
            deadline = offer.get("eligible_until")
            if deadline:
                if aware_iso(deadline, "eligible_until") <= now:
                    raise ValueError("Expired theater offers must be archived before publication.")
