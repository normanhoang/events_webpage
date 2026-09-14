from datetime import datetime, time, timedelta

from django.core.paginator import Paginator
from django.conf import settings
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_safe

from .forms import EventFilters
from .models import Event, Occurrence


@require_safe
def health(request):
    response = JsonResponse({
        "status": "ok",
        "revision": settings.DEPLOYMENT_REVISION,
        "upcoming_occurrences": Occurrence.objects.upcoming().count(),
    })
    response["Cache-Control"] = "no-store"
    return response


def filtered_occurrences(occurrences, data):
    if category := data.get("category"):
        occurrences = occurrences.filter(event__category=category)
    if neighborhood := data.get("neighborhood"):
        occurrences = occurrences.filter(event__neighborhood__iexact=neighborhood)
    if day := data.get("date"):
        start = timezone.make_aware(datetime.combine(day, time.min))
        end = start + timedelta(days=1)
        occurrences = occurrences.filter(starts_at__lt=end).filter(
            Q(ends_at__gt=start) | Q(ends_at__isnull=True, starts_at__gte=start)
        )
    for parameter, lookup in [("min_price", "gte"), ("max_price", "lte")]:
        if (value := data.get(parameter)) is not None:
            occurrences = occurrences.filter(event__currency="USD", **{f"event__price_min__{lookup}": value})
    if data.get("free"):
        occurrences = occurrences.filter(event__price_min=0, event__price_max=0)
    return occurrences


@require_safe
def home(request):
    occurrences = Occurrence.objects.upcoming().select_related("event").order_by("starts_at", "pk")
    form = EventFilters(request.GET)
    occurrences = filtered_occurrences(occurrences, form.cleaned_data) if form.is_valid() else occurrences.none()
    total_count = occurrences.count()
    top_picks = []
    top_pick_event_ids = set()
    for occurrence in occurrences.filter(event__top_pick=True):
        if occurrence.event_id not in top_pick_event_ids:
            top_picks.append(occurrence)
            top_pick_event_ids.add(occurrence.event_id)
    remaining = occurrences.exclude(event_id__in=top_pick_event_ids)
    return render(request, "events/home.html", {
        "page_obj": Paginator(remaining, 12).get_page(request.GET.get("page")),
        "top_picks": top_picks,
        "total_count": total_count,
        "form": form,
        "categories": Event.Category.choices,
        "neighborhoods": Event.objects.filter(occurrences__in=Occurrence.objects.upcoming()).exclude(neighborhood="").order_by("neighborhood").values_list("neighborhood", flat=True).distinct(),
    })


@require_safe
def event_detail(request, slug):
    event = get_object_or_404(Event, slug=slug)
    occurrences = list(event.occurrences.upcoming())
    if not occurrences:
        raise Http404("No upcoming dates for this event.")
    return render(request, "events/detail.html", {"event": event, "occurrences": occurrences})
