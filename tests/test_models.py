from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone


@pytest.mark.django_db
def test_event_and_occurrence_preserve_editorial_and_source_data():
    from events.models import Event, Occurrence

    now = timezone.now()
    event = Event.objects.create(
        title="Gallery evening", slug="gallery-evening", description="An open studio.",
        category="art", tags=["Art", "Indoors"], venue="Studio", neighborhood="Chelsea",
        borough="Manhattan", official_url="https://example.org/gallery",
        image_url="https://example.org/photo.jpg", price_label="From $10",
        price_min=Decimal("10"), price_max=Decimal("20"), currency="USD",
        fit_reason="A relaxed way to discover local art.", verified_at=now, top_pick=True,
    )
    occurrence = Occurrence.objects.create(event=event, source_key="gallery-2026-10-01",
                                           starts_at=now, ends_at=now + timedelta(hours=2))
    event.refresh_from_db()
    assert event.tags == ["Art", "Indoors"]
    assert event.price_min == Decimal("10")
    assert event.created_at <= event.updated_at
    assert event.verified_at == now
    assert str(event) == "Gallery evening"
    assert occurrence in event.occurrences.all()
    assert occurrence.created_at <= occurrence.updated_at
    assert "Gallery evening" in str(occurrence)
    assert event.get_absolute_url() == "/events/gallery-evening/"


@pytest.mark.django_db
def test_prices_cannot_be_negative_or_reversed():
    from django.db import IntegrityError, transaction
    from events.models import Event

    for minimum, maximum in [(-1, 10), (0, -1), (20, 10)]:
        with pytest.raises(IntegrityError), transaction.atomic():
            Event.objects.create(title="Invalid", slug="invalid", official_url="https://example.org",
                                 price_min=minimum, price_max=maximum)


@pytest.mark.django_db
def test_occurrence_rejects_reversed_times_and_duplicate_source_keys():
    from django.db import IntegrityError, transaction
    from events.models import Event, Occurrence

    event = Event.objects.create(title="Show", slug="show", official_url="https://example.org/show")
    now = timezone.now()
    with pytest.raises(IntegrityError), transaction.atomic():
        Occurrence.objects.create(event=event, source_key="bad", starts_at=now, ends_at=now - timedelta(seconds=1))
    Occurrence.objects.create(event=event, source_key="one", starts_at=now)
    with pytest.raises(IntegrityError), transaction.atomic():
        Occurrence.objects.create(event=event, source_key="one", starts_at=now)


def test_public_occurrences_exclude_expired_but_keep_ongoing_and_database_records(make_occurrence):
    from events.models import Occurrence

    now = timezone.now()
    past = make_occurrence(starts_at=now - timedelta(days=2), ends_at=now - timedelta(days=1))
    no_end_past = make_occurrence(starts_at=now - timedelta(seconds=1))
    boundary = make_occurrence(starts_at=now - timedelta(hours=1), ends_at=now)
    ongoing = make_occurrence(starts_at=now - timedelta(hours=1), ends_at=now + timedelta(hours=1))
    future = make_occurrence(starts_at=now + timedelta(days=1))
    assert list(Occurrence.objects.upcoming(at=now)) == [ongoing, future]
    assert Occurrence.objects.filter(pk__in=[past.pk, no_end_past.pk, boundary.pk]).count() == 3


def test_categories_cover_normans_public_interests():
    from events.models import Event

    assert {"theater", "queer", "tech", "comics", "gaming", "books", "fitness", "music", "food"} <= set(Event.Category.values)
