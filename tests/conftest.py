from datetime import timedelta
from itertools import count

import pytest
from django.utils import timezone

from events.models import Event, Occurrence


@pytest.fixture
def make_occurrence(db):
    sequence = count(1)

    def make(*, starts_at=None, ends_at=None, **fields):
        number = next(sequence)
        defaults = dict(title=f"Event {number}", slug=f"event-{number}",
                        official_url=f"https://example.org/events/{number}", description="A lovely NYC outing.")
        defaults.update(fields)
        event = Event.objects.create(**defaults)
        return Occurrence.objects.create(event=event, source_key=f"slot-{number}",
                                         starts_at=starts_at or timezone.now() + timedelta(days=1), ends_at=ends_at)

    return make
