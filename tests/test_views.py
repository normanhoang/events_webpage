from datetime import timedelta

import pytest
from django.utils import timezone

pytestmark = pytest.mark.django_db


def test_health_reports_deployed_revision_and_public_catalog_size(client, make_occurrence, settings):
    settings.DEPLOYMENT_REVISION = "abc123"
    make_occurrence(title="Visible")
    hidden = make_occurrence(title="Hidden")
    hidden.is_active = False
    hidden.save()

    response = client.get("/health/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "revision": "abc123", "upcoming_occurrences": 1}
    assert response.headers["Cache-Control"] == "no-store"


def test_home_shows_top_picks_section_then_chronological_occurrences(client, make_occurrence):
    now = timezone.now()
    later = make_occurrence(title="Later ordinary", starts_at=now + timedelta(days=3))
    early = make_occurrence(title="Earlier ordinary", starts_at=now + timedelta(days=1))
    pick = make_occurrence(title="Norman recommends", top_pick=True, starts_at=now + timedelta(days=4))
    expired = make_occurrence(title="Expired show", starts_at=now - timedelta(days=1))
    response = client.get("/")
    assert list(response.context["top_picks"]) == [pick]
    assert list(response.context["page_obj"]) == [early, later]
    body = response.content.decode()
    assert "Top picks for Norman" in body
    assert body.index(pick.event.title) < body.index(early.event.title) < body.index(later.event.title)
    assert body.count(pick.event.title) == 1
    assert expired.event.title not in body
    assert pick.event.get_absolute_url() in body


def test_home_shows_every_top_pick_once_and_paginates_only_non_top_pick_occurrences(client, make_occurrence):
    from events.models import Occurrence

    now = timezone.now()
    picks = [
        make_occurrence(title=f"Top pick {number}", top_pick=True,
                        starts_at=now + timedelta(days=number))
        for number in range(1, 6)
    ]
    Occurrence.objects.create(event=picks[0].event, source_key="second-date",
                              starts_at=now + timedelta(days=10))
    ordinary = [
        make_occurrence(title=f"Ordinary {number}", starts_at=now + timedelta(days=20 + number))
        for number in range(13)
    ]

    first = client.get("/")
    assert list(first.context["top_picks"]) == picks
    assert list(first.context["page_obj"]) == ordinary[:12]
    assert first.context["page_obj"].paginator.per_page == 12
    body = first.content.decode()
    for pick in picks:
        assert body.count(pick.event.title) == 1

    second = client.get("/", {"page": "2"})
    assert list(second.context["top_picks"]) == picks
    assert list(second.context["page_obj"]) == ordinary[12:]


def test_category_filter(client, make_occurrence):
    art = make_occurrence(category="art")
    make_occurrence(category="music")
    assert list(client.get("/", {"category": "art"}).context["page_obj"]) == [art]


def test_neighborhood_filter(client, make_occurrence):
    chelsea = make_occurrence(neighborhood="Chelsea")
    make_occurrence(neighborhood="Astoria")
    assert list(client.get("/", {"neighborhood": "chelsea"}).context["page_obj"]) == [chelsea]


def test_date_filter_uses_new_york_calendar_and_includes_spanning_occurrences(client, make_occurrence):
    from datetime import datetime, timezone as dt_timezone

    late = make_occurrence(starts_at=datetime(2030, 5, 2, 2, tzinfo=dt_timezone.utc))
    spanning = make_occurrence(starts_at=datetime(2030, 4, 30, 12, tzinfo=dt_timezone.utc),
                               ends_at=datetime(2030, 5, 2, 5, tzinfo=dt_timezone.utc))
    make_occurrence(starts_at=datetime(2030, 5, 2, 5, tzinfo=dt_timezone.utc))
    make_occurrence(starts_at=datetime(2030, 4, 30, 12, tzinfo=dt_timezone.utc),
                    ends_at=datetime(2030, 5, 1, 4, tzinfo=dt_timezone.utc))
    response = client.get("/", {"date": "2030-05-01"})
    assert set(response.context["page_obj"]) == {late, spanning}


def test_price_filter_limits_known_usd_starting_prices_inclusively(client, make_occurrence):
    match = make_occurrence(price_min=10, price_max=30)
    make_occurrence(price_min=9)
    make_occurrence(price_min=21)
    make_occurrence(price_min=None)
    make_occurrence(price_min=10, currency="EUR")
    assert list(client.get("/", {"min_price": "10", "max_price": "20"}).context["page_obj"]) == [match]


def test_free_filter_excludes_unknown_and_paid_ranges(client, make_occurrence):
    free = make_occurrence(price_min=0, price_max=0)
    make_occurrence(price_min=0, price_max=20)
    make_occurrence(price_min=None, price_label="Check source")
    make_occurrence(price_min=10, price_max=10)
    assert list(client.get("/", {"free": "1"}).context["page_obj"]) == [free]


def test_invalid_filter_values_show_errors_without_server_errors(client, make_occurrence):
    make_occurrence()
    for query in [{"date": "nonsense"}, {"date": "9999-12-31"}, {"max_price": "NaN"},
                  {"min_price": "-1"}, {"max_price": "Infinity"}, {"min_price": "20", "max_price": "10"},
                  {"category": "unknown"}, {"max_price": "999999999999999999999"}]:
        response = client.get("/", query)
        assert response.status_code == 200
        assert b"Please correct the filters" in response.content
        assert not list(response.context["page_obj"])


def test_pagination_has_twelve_per_page_and_preserves_all_query_parameters(client, make_occurrence):
    from html import unescape

    for _ in range(14):
        make_occurrence(category="art", title="Art & walks", price_min=0, price_max=0, neighborhood="West Village")
    query = {"category": "art", "neighborhood": "West Village", "free": "1"}
    first = client.get("/", query)
    assert len(first.context["page_obj"]) == 12
    assert '?category=art&neighborhood=West+Village&free=1&page=2' in unescape(first.content.decode())
    second = client.get("/", {**query, "page": "2"})
    assert len(second.context["page_obj"]) == 2
    assert not set(first.context["page_obj"]) & set(second.context["page_obj"])
    assert 'page=1' in second.content.decode()
    assert client.get("/", {**query, "page": "bad"}).context["page_obj"].number == 1
    assert client.get("/", {**query, "page": "999"}).context["page_obj"].number == 2


def test_empty_state_offers_a_filter_reset(client, make_occurrence):
    # A real event exists, so the emptiness is caused by the filter rather than by an empty
    # database; the old trigger was a search term, which is gone.
    make_occurrence(neighborhood="Chelsea")
    response = client.get("/", {"neighborhood": "Nowhere"})
    assert b"No upcoming events found" in response.content
    assert b"Try another date, interest, or neighborhood." in response.content
    assert b'href="/">Clear filters' in response.content


def test_a_stale_search_parameter_renders_the_full_list_instead_of_erroring(client, make_occurrence):
    # Old ?q= links are in the wild. The parameter is simply no longer read, so it must fall
    # through to the unfiltered list rather than erroring or 404ing.
    make_occurrence(title="Jazz night")
    make_occurrence(title="Pottery class")

    response = client.get("/", {"q": "jazz"})

    assert response.status_code == 200
    assert len(response.context["page_obj"]) == 2


def test_detail_shows_editorial_data_upcoming_times_and_official_cta(client, make_occurrence):
    from events.models import Occurrence

    occurrence = make_occurrence(title="Open studio", description="Meet the artists.", category="art",
                                 venue="The Studio", neighborhood="Chelsea", borough="Manhattan",
                                 fit_reason="A little creative inspiration.", tags=["Art", "Indoors"],
                                 price_label="From $12", price_min=12, price_max=25, verified_at=timezone.now())
    later = Occurrence.objects.create(event=occurrence.event, source_key="later", starts_at=timezone.now() + timedelta(days=3))
    Occurrence.objects.create(event=occurrence.event, source_key="past", starts_at=timezone.now() - timedelta(days=1))
    response = client.get(occurrence.event.get_absolute_url())
    assert response.status_code == 200
    assert list(response.context["occurrences"]) == [occurrence, later]
    body = response.content.decode()
    for text in ["Open studio", "Meet the artists.", "The Studio", "Chelsea", "Manhattan", "Art", "Indoors",
                 "A little creative inspiration.", "From $12", "Verified", "Official source"]:
        assert text in body
    assert f'href="{occurrence.event.official_url}"' in body
    assert 'rel="noopener noreferrer"' in body
    assert client.get("/events/unknown/").status_code == 404


def test_expired_only_event_detail_is_not_public_but_record_remains(client, make_occurrence):
    from events.models import Event, Occurrence

    expired = make_occurrence(starts_at=timezone.now() - timedelta(days=1))
    assert client.get(expired.event.get_absolute_url()).status_code == 404
    assert Event.objects.filter(pk=expired.event_id).exists()
    assert Occurrence.objects.filter(pk=expired.pk).exists()


def test_public_pages_are_read_only(client, make_occurrence):
    from events.models import Event, Occurrence

    occurrence = make_occurrence()
    for path in ["/", occurrence.event.get_absolute_url()]:
        assert client.post(path, {"title": "Changed"}).status_code == 405
        assert client.put(path, data="{}").status_code == 405
        assert client.delete(path).status_code == 405
        assert client.head(path).status_code == 200
    assert Event.objects.count() == Occurrence.objects.count() == 1


def test_home_has_accessible_get_filters_public_interest_chips_and_editorial_cards(client, make_occurrence):
    occurrence = make_occurrence(category="art", neighborhood="Chelsea", venue="Open Studio",
                                 price_label="Free", price_min=0, price_max=0, top_pick=True,
                                 fit_reason="Meet local makers.")
    body = client.get("/", {"category": "art"}).content.decode()
    for text in ['method="get"', 'href="#main"', 'id="main"', 'name="date"',
                 'name="category"', 'name="neighborhood"', 'name="min_price"', 'name="max_price"',
                 'name="free"', 'value="art" selected', 'Art &amp; culture',
                 'Food &amp; drink', 'Outdoors', 'Music', 'Community', 'Top pick', 'Meet local makers.',
                 'Open Studio', 'Free', 'events/site.css', 'class="event-grid"', 'datetime=']:
        assert text in body
    # Search was removed deliberately: the catalog is small enough that interest chips plus
    # filters cover discovery, so a search box must not creep back in unnoticed.
    assert 'name="q"' not in body
    assert 'for="id_q"' not in body
    assert occurrence.event.get_absolute_url() in body
    assert 'rel="icon"' in body
    from django.contrib.staticfiles import finders
    assert finders.find("events/favicon.svg")


def test_remote_images_have_local_category_backdrops_and_failure_handler(client, make_occurrence):
    from django.contrib.staticfiles import finders
    from events.models import Event

    occurrence = make_occurrence(category="art", image_url="https://example.org/missing.jpg")
    for path in ["/", occurrence.event.get_absolute_url()]:
        body = client.get(path).content.decode()
        assert 'src="https://example.org/missing.jpg"' in body
        assert 'data-remote-image' in body
        assert 'referrerpolicy="no-referrer"' in body
        assert 'events/images/art.svg' in body
        assert 'events/images.js' in body
        assert 'class="image-fallback"' in body
    for category, _ in Event.Category.choices:
        occurrence.event.category = category
        assert finders.find(occurrence.event.fallback_image)
    occurrence.event.image_url = ""
    occurrence.event.save()
    assert 'data-remote-image' not in client.get(occurrence.event.get_absolute_url()).content.decode()
