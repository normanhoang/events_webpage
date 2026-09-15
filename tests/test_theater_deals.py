import json
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

pytestmark = pytest.mark.django_db


def sample_deal():
    return {
        "title": "Example Musical",
        "slug": "example-musical",
        "classification": "broadway",
        "venue": "Example Theatre",
        "neighborhood": "Theater District",
        "official_url": "https://example.org/shows/example-musical",
        "image_url": "",
        "verified_at": "2030-01-01T12:00:00-05:00",
        "offers": [
            {
                "source_key": "digital-rush",
                "label": "Digital rush",
                "price_label": "$49 before fees",
                "price_min": "49.00",
                "price_max": "49.00",
                "fees_included": False,
                "restrictions": "Daily at 9 AM; limit two.",
                "eligible_until": "2030-05-20T23:59:59-04:00",
                "official_url": "https://example.org/deals/example-musical-rush",
            },
            {
                "source_key": "lottery",
                "label": "Digital lottery",
                "price_label": "$45 before fees",
                "price_min": "45.00",
                "price_max": "45.00",
                "fees_included": False,
                "restrictions": "Enter the day before the performance.",
                "eligible_until": "2030-05-20T23:59:59-04:00",
                "official_url": "https://example.org/deals/example-musical-lottery",
            },
        ],
    }


def test_import_theater_deals_groups_multiple_offers_under_one_active_show(tmp_path):
    from events.models import TheaterDeal, TheaterOffer

    path = tmp_path / "theater-deals.json"
    path.write_text(json.dumps([sample_deal()]))

    call_command("import_theater_deals", str(path), sync=True)

    deal = TheaterDeal.objects.get()
    assert deal.title == "Example Musical"
    assert deal.classification == TheaterDeal.Classification.BROADWAY
    assert deal.is_active is True
    assert list(deal.offers.order_by("price_min", "source_key").values_list("source_key", flat=True)) == [
        "lottery", "digital-rush"
    ]
    assert TheaterOffer.objects.filter(deal=deal, is_active=True).count() == 2


def test_import_theater_deals_accepts_unknown_fee_status(tmp_path):
    path = tmp_path / "theater-deals.json"
    record = sample_deal()
    record["offers"][0]["fees_included"] = None
    path.write_text(json.dumps([record]))

    call_command("import_theater_deals", str(path), sync=True)

    from events.models import TheaterOffer
    assert TheaterOffer.objects.get(source_key="digital-rush").fees_included is None


def test_import_theater_deals_rejects_more_than_fifty_active_shows(tmp_path):
    from django.core.management.base import CommandError

    path = tmp_path / "theater-deals.json"
    records = []
    for index in range(51):
        record = sample_deal()
        record["title"] = f"Show {index}"
        record["official_url"] = f"https://example.org/shows/{index}"
        record["offers"][0]["official_url"] = f"https://example.org/deals/{index}-rush"
        record["offers"][1]["official_url"] = f"https://example.org/deals/{index}-lottery"
        records.append(record)
    path.write_text(json.dumps(records))

    with pytest.raises(CommandError, match="at most 50"):
        call_command("import_theater_deals", str(path), sync=True)


def test_theater_sync_deactivates_missing_deals_without_touching_regular_events(tmp_path, make_occurrence):
    from events.models import TheaterDeal, TheaterOffer

    regular = make_occurrence(title="Ordinary event")
    path = tmp_path / "theater-deals.json"
    path.write_text(json.dumps([sample_deal()]))
    call_command("import_theater_deals", str(path), sync=True)

    path.write_text("[]")
    call_command("import_theater_deals", str(path), sync=True)

    deal = TheaterDeal.objects.get()
    assert deal.is_active is False
    assert TheaterOffer.objects.get(deal=deal, source_key="lottery").is_active is False
    regular.event.refresh_from_db()
    regular.refresh_from_db()
    assert regular.event.is_active is True
    assert regular.is_active is True


def test_active_deal_records_exclude_expired_offers_but_preserve_history():
    from events.models import TheaterDeal, TheaterOffer

    deal = TheaterDeal.objects.create(
        title="Current production", slug="current-production", classification="off_broadway",
        official_url="https://example.org/current", verified_at=timezone.now(),
    )
    expired = TheaterOffer.objects.create(
        deal=deal, source_key="expired", label="Expired rush", price_label="$30", price_min=30,
        official_url="https://example.org/current/expired",
        eligible_until=timezone.now() - timedelta(minutes=1),
    )
    live = TheaterOffer.objects.create(
        deal=deal, source_key="live", label="Live rush", price_label="$30", price_min=30,
        official_url="https://example.org/current/live",
        eligible_until=timezone.now() + timedelta(days=1),
    )

    assert list(TheaterOffer.objects.active()) == [live]
    assert TheaterOffer.objects.filter(pk=expired.pk).exists()


def make_deal(*, title, classification, verified_at=None, price=49, offer_label="Digital rush",
              image_url=""):
    from events.models import TheaterDeal, TheaterOffer

    slug = title.lower().replace(" ", "-")
    deal = TheaterDeal.objects.create(
        title=title, slug=slug, classification=classification,
        official_url=f"https://example.org/shows/{slug}", verified_at=verified_at or timezone.now(),
        venue="Example Theatre", neighborhood="Theater District", image_url=image_url,
    )
    TheaterOffer.objects.create(
        deal=deal, source_key="main", label=offer_label, price_label=f"${price}", price_min=price,
        official_url=f"https://example.org/deals/{slug}", eligible_until=timezone.now() + timedelta(days=2),
    )
    return deal


def test_theater_card_renders_the_official_show_image_when_available(client):
    # The events side already renders a remote image with a local fallback underneath; the theater
    # card must use the same mechanism so a blocked or broken poster degrades instead of vanishing.
    url = "https://example.org/art/broadway-first.jpg"
    make_deal(title="Broadway First", classification="broadway", image_url=url)

    body = client.get("/theater-deals/").content.decode()

    assert f'src="{url}"' in body
    assert "data-remote-image" in body
    assert "referrerpolicy=\"no-referrer\"" in body


def test_theater_card_falls_back_to_local_art_when_a_show_has_no_image(client):
    make_deal(title="Off Broadway Second", classification="off_broadway")

    body = client.get("/theater-deals/").content.decode()

    assert "theater-off-broadway.svg" in body
    assert "data-remote-image" not in body


def test_theater_deal_fallback_image_maps_each_classification_to_its_own_art():
    from events.models import TheaterDeal

    seen = set()
    for classification in TheaterDeal.Classification.values:
        deal = TheaterDeal(classification=classification)
        expected = f"events/images/theater-{classification.replace('_', '-')}.svg"
        assert deal.fallback_image == expected
        seen.add(deal.fallback_image)
    assert len(seen) == len(TheaterDeal.Classification.values)


def test_theater_fallback_art_exists_for_every_classification():
    from pathlib import Path

    from events.models import TheaterDeal

    root = Path(__file__).resolve().parents[1] / "events/static/events/images"
    for classification in TheaterDeal.Classification.values:
        asset = root / f"theater-{classification.replace('_', '-')}.svg"
        assert asset.exists(), asset
        svg = asset.read_text()
        assert 'viewBox="0 0 800 600"' in svg, "illustration art must be 4:3 for the card image box"
        assert "lucide" in svg, "keep the provenance comment"
        assert "currentColor" not in svg, "an <img> cannot inherit colour; bake the ink"


def test_validate_theater_deals_rejects_unsafe_image_urls():
    from automation.theater_deals import validate_theater_deals

    # image_url lands in a template src attribute, so anything but https must be refused. A
    # non-string must also surface as ValueError, which is the only type the publisher catches.
    for bad in ["javascript:alert(1)", "http://example.org/art.jpg",
                "data:image/svg+xml,<svg onload=alert(1)>", 42, {"u": 1}]:
        record = sample_deal()
        record["verified_at"] = timezone.now().isoformat()
        record["image_url"] = bad
        with pytest.raises(ValueError, match="image_url"):
            validate_theater_deals([record], now=timezone.now())


def test_validate_theater_deals_allows_a_missing_or_https_image_url():
    from automation.theater_deals import validate_theater_deals

    for good in ["", None, "https://example.org/art.jpg"]:
        record = sample_deal()
        record["verified_at"] = timezone.now().isoformat()
        record["image_url"] = good
        validate_theater_deals([record], now=timezone.now())


def test_theater_deals_page_groups_offers_by_show_and_prioritizes_classifications(client):
    broadway = make_deal(title="Broadway First", classification="broadway", price=49)
    off_broadway = make_deal(title="Off Broadway Second", classification="off_broadway", price=39)
    other = make_deal(title="Opera Third", classification="other", price=25)

    response = client.get("/theater-deals/")

    assert response.status_code == 200
    body = response.content.decode()
    assert body.index(broadway.title) < body.index(off_broadway.title) < body.index(other.title)
    # Assert the visible heading, not "Theater deals" — that string also appears in the <title>,
    # so it passed even when the page's own nav link was removed.
    assert "NYC theater deals" in body
    assert "All theater" in body
    assert "Broadway" in body
    assert "Off-Broadway" in body
    assert "Other NYC theater" in body
    # The hero's own "Last checked" line is retired: this page states its freshness in the
    # shared footer, exactly like the events page.
    assert "Last checked" not in body
    assert "last-checked" not in body
    footer = body.split('<footer class="site-footer')[1].split("</footer>")[0]
    assert "last-updated" in footer
    assert "Digital rush" in body
    assert 'href="?type=off_broadway"' in body
    assert 'href="?type=other"' in body


def test_theater_footer_states_when_the_deals_were_last_verified(client):
    from django.template.defaultfilters import date as date_filter

    # Both inside the seven-day freshness window the page enforces, so both qualify. The older
    # deal is deliberately the one a type bubble filters down to, so a page-level aggregate
    # would report the older moment and this test would catch it.
    older = timezone.now() - timedelta(days=5, hours=3)
    newest = timezone.now() - timedelta(hours=9)
    make_deal(title="Newest Show", classification="broadway", verified_at=newest)
    make_deal(title="Older Show", classification="other", verified_at=older)

    # New York time, like the events page — and a type bubble is a view of the same dataset,
    # so it must not move the freshness line. The template renders the stored UTC value in New
    # York time, so the expectation is built from the same conversion rather than the raw value.
    expected = date_filter(timezone.localtime(newest), "M j, Y · g:i A") + " New York"
    older_rendered = date_filter(timezone.localtime(older), "M j, Y · g:i A")
    for path in ["/theater-deals/", "/theater-deals/?type=other"]:
        footer = client.get(path).content.decode().split('<footer class="site-footer')[1].split("</footer>")[0]
        assert expected in footer, path
        assert older_rendered not in footer, path


def test_theater_deals_page_type_bubbles_filter_shows_and_retired_params_do_not_propagate(client):
    make_deal(title="Broadway First", classification="broadway")
    off_broadway = make_deal(title="Off Broadway Second", classification="off_broadway")

    response = client.get("/theater-deals/", {"type": "off_broadway", "q": "stale"})

    assert response.status_code == 200
    assert list(response.context["deals"]) == [off_broadway]
    body = response.content.decode()
    assert "Broadway First" not in body
    assert "Off Broadway Second" in body
    assert "q=stale" not in body
    assert 'aria-current="true"' in body


def test_theater_deals_page_hides_shows_without_live_offers(client):
    from events.models import TheaterOffer

    stale = make_deal(title="Stale Deal", classification="broadway")
    TheaterOffer.objects.filter(deal=stale).update(eligible_until=timezone.now() - timedelta(seconds=1))

    response = client.get("/theater-deals/")

    assert response.status_code == 200
    assert "Stale Deal" not in response.content.decode()
    assert "No verified theater deals right now" in response.content.decode()


def test_theater_deals_page_hides_unverified_open_ended_offers(client):
    stale = make_deal(
        title="Stale verification", classification="broadway",
        verified_at=timezone.now() - timedelta(days=8),
    )
    assert stale.offers.get().eligible_until is not None
    stale.offers.update(eligible_until=None)

    response = client.get("/theater-deals/")

    assert response.status_code == 200
    assert "Stale verification" not in response.content.decode()


def test_archive_theater_deals_removes_expired_offers_and_preserves_monthly_history(tmp_path):
    archive_dir = tmp_path / "theater-archive"
    active_path = tmp_path / "theater-deals.json"
    record = sample_deal()
    record["offers"][0]["eligible_until"] = "2030-05-01T23:59:59-04:00"
    record["offers"][1]["eligible_until"] = "2030-05-03T23:59:59-04:00"
    active_path.write_text(json.dumps([record]))

    call_command(
        "archive_theater_deals", active_path=str(active_path), archive_dir=str(archive_dir),
        now="2030-05-02T12:00:00-04:00",
    )

    active = json.loads(active_path.read_text())
    archive = json.loads((archive_dir / "2030-05.json").read_text())
    assert [offer["source_key"] for offer in active[0]["offers"]] == ["lottery"]
    assert archive[0]["title"] == "Example Musical"
    assert archive[0]["archive_reason"] == "expired"
    assert [offer["source_key"] for offer in archive[0]["offers"]] == ["digital-rush"]


def test_theater_archive_lock_refuses_a_preexisting_symbolic_link(tmp_path, monkeypatch):
    from events.management.commands.archive_events import archive_lock_path

    runtime = tmp_path / "runtime"
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    active_path = tmp_path / "theater-deals.json"
    active_path.write_text(json.dumps([sample_deal()]))
    victim = tmp_path / "victim.txt"
    victim.write_text("do not truncate")
    lock_path = archive_lock_path(active_path)
    lock_path.symlink_to(victim)

    with pytest.raises(OSError):
        call_command(
            "archive_theater_deals", active_path=str(active_path), archive_dir=str(tmp_path / "archive"),
            now="2030-05-02T12:00:00-04:00",
        )

    assert victim.read_text() == "do not truncate"


def test_validate_theater_deals_raises_value_error_never_attribute_error():
    from automation.theater_deals import validate_theater_deals

    # The publisher only catches ValueError; an AttributeError here would escape and abort the
    # events publish, so every malformed shape must normalise to ValueError.
    now = timezone.now()
    for records in [["not-a-record"], [None], [42], [["nested"]]]:
        with pytest.raises(ValueError):
            validate_theater_deals(records, now=now)
    with pytest.raises(ValueError):
        validate_theater_deals({"not": "an array"}, now=now)


def test_validate_theater_deals_rejects_non_string_timestamps_as_value_error():
    from automation.theater_deals import validate_theater_deals

    now = timezone.now()
    record = sample_deal()
    record["verified_at"] = 1893456000
    with pytest.raises(ValueError, match="verified_at"):
        validate_theater_deals([record], now=now)

    record = sample_deal()
    record["verified_at"] = (timezone.now() - timedelta(minutes=1)).isoformat()
    record["offers"][0]["eligible_until"] = 1893456000
    with pytest.raises(ValueError, match="eligible_until"):
        validate_theater_deals([record], now=now)


def test_archive_theater_deals_refuses_malformed_records_instead_of_dropping_them(tmp_path):
    from django.core.management.base import CommandError

    # split_expired_offers keeps only records with live offers and archives only expired ones,
    # so a record with no offers would previously disappear with no archive entry at all.
    for bad_record in [
        {"title": "Orphan Show", "official_url": "https://example.org/orphan"},
        {"title": "Empty offers", "official_url": "https://example.org/empty", "offers": []},
        "not-a-record",
    ]:
        path = tmp_path / "theater-deals.json"
        path.write_text(json.dumps([bad_record]))
        with pytest.raises(CommandError):
            call_command(
                "archive_theater_deals", active_path=str(path),
                archive_dir=str(tmp_path / "archive"), now="2030-05-02T12:00:00-04:00",
            )


def test_discovery_rotation_reaches_every_source_inside_one_window():
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    from automation.theater_deals import DISCOVERY_PER_NIGHT, DISCOVERY_SOURCES, discovery_sources_for

    # Re-verifying and pruning can only shrink the page, so the rotation is the only source of growth.
    # Ranked rotation, not hashing: every source must be reached inside one window, or the tail of the
    # list starves and those sources are never swept.
    window = -(-len(DISCOVERY_SOURCES) // DISCOVERY_PER_NIGHT)
    nyc = ZoneInfo("America/New_York")
    start = datetime(2026, 9, 15, 0, 5, tzinfo=nyc)

    seen = set()
    for offset in range(window):
        tonight = discovery_sources_for(start + timedelta(days=offset))
        assert len(tonight) == DISCOVERY_PER_NIGHT
        assert len(set(tonight)) == DISCOVERY_PER_NIGHT, "a night must not repeat a source"
        seen.update(tonight)
    assert seen == set(DISCOVERY_SOURCES)


def test_discovery_slot_resolves_in_new_york_time():
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    from automation.theater_deals import discovery_sources_for

    # 00:30 UTC on Sep 15 is still Sep 14 in New York. Resolving the slot from the raw date would
    # advance a day early and skip or repeat a slot, so the local day has to decide.
    nyc = ZoneInfo("America/New_York")
    late_local = datetime(2026, 9, 14, 20, 30, tzinfo=nyc)
    same_moment_utc = late_local.astimezone(timezone.utc)
    assert discovery_sources_for(same_moment_utc) == discovery_sources_for(late_local)

    next_local_day = datetime(2026, 9, 15, 20, 30, tzinfo=nyc)
    assert discovery_sources_for(next_local_day) != discovery_sources_for(late_local)


def test_theater_qualification_ceilings_stay_consistent():
    from automation.theater_deals import (
        MAX_QUALIFYING_PRICE,
        MAX_QUALIFYING_RUSH_PRICE,
        MIN_QUALIFYING_DISCOUNT_PCT,
        MIN_QUALIFYING_PRICE_DROP,
    )

    # The briefing prints these numbers, so they live in the module rather than in two prose copies.
    for value in (MAX_QUALIFYING_PRICE, MAX_QUALIFYING_RUSH_PRICE, MIN_QUALIFYING_DISCOUNT_PCT, MIN_QUALIFYING_PRICE_DROP):
        assert isinstance(value, int) and value > 0
    # A rush or lottery is meant to be the cheapest path in, so its ceiling cannot exceed the
    # general price ceiling; a discount floor above 100% would qualify nothing.
    assert MAX_QUALIFYING_RUSH_PRICE <= MAX_QUALIFYING_PRICE
    assert MIN_QUALIFYING_DISCOUNT_PCT <= 100
