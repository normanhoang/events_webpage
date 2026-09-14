import pytest
from django.contrib import admin

from events.models import Event, Occurrence


@pytest.mark.django_db
def test_admin_registers_searchable_events_with_occurrence_inline_and_history(admin_client, make_occurrence):
    occurrence = make_occurrence()
    assert Event in admin.site._registry
    assert Occurrence in admin.site._registry
    event_admin = admin.site._registry[Event]
    assert {"title", "official_url", "venue", "neighborhood"} <= set(event_admin.search_fields)
    assert {"category", "borough", "top_pick", "is_active"} <= set(event_admin.list_filter)
    assert any(inline.model is Occurrence for inline in event_admin.inlines)
    assert {"created_at", "updated_at"} <= set(event_admin.readonly_fields)
    for path in ["/admin/events/event/", f"/admin/events/event/{occurrence.event_id}/change/", "/admin/events/occurrence/"]:
        assert admin_client.get(path).status_code == 200
