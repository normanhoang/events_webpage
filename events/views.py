from django.conf import settings
from django.core.paginator import Paginator
from django.http import Http404, JsonResponse, QueryDict
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_safe

from .models import Event, Occurrence

# The page offers exactly two filters: an interest chip and the free-only toggle. Everything
# else that used to be here (search, date, neighborhood, price range) was removed deliberately —
# the catalog is small enough that a fixed taxonomy plus the card details beat a filter panel.
FILTERS = ("category", "free")


@require_safe
def health(request):
    response = JsonResponse({
        "status": "ok",
        "revision": settings.DEPLOYMENT_REVISION,
        "upcoming_occurrences": Occurrence.objects.upcoming().count(),
    })
    response["Cache-Control"] = "no-store"
    return response


def active_filters(params):
    """Keep only the filters the page still offers, so retired params cannot linger."""
    return {name: params[name] for name in FILTERS if params.get(name)}


def filtered_occurrences(occurrences, params):
    category = params.get("category")
    if category:
        # The chip row only ever emits real categories, so an unknown one is a stale or
        # hand-edited link. Show nothing rather than silently ignoring what was asked for.
        if category not in Event.Category.values:
            return occurrences.none()
        occurrences = occurrences.filter(event__category=category)
    if params.get("free"):
        occurrences = occurrences.filter(event__price_min=0, event__price_max=0)
    return occurrences


@require_safe
def home(request):
    params = active_filters(request.GET)
    occurrences = Occurrence.objects.upcoming().select_related("event").order_by("starts_at", "pk")
    occurrences = filtered_occurrences(occurrences, params)
    total_count = occurrences.count()
    top_picks = []
    top_pick_event_ids = set()
    for occurrence in occurrences.filter(event__top_pick=True):
        if occurrence.event_id not in top_pick_event_ids:
            top_picks.append(occurrence)
            top_pick_event_ids.add(occurrence.event_id)
    remaining = occurrences.exclude(event_id__in=top_pick_event_ids)
    # Hand the template a whitelisted query so chip and pagination links can only ever carry
    # supported filters, instead of copying whatever happened to be in the URL.
    cleaned = QueryDict("", mutable=True)
    for name, value in params.items():
        cleaned[name] = value
    return render(request, "events/home.html", {
        "page_obj": Paginator(remaining, 12).get_page(request.GET.get("page")),
        "top_picks": top_picks,
        "total_count": total_count,
        "form_query": cleaned,
        "active_category": params.get("category", ""),
        "free_active": bool(params.get("free")),
        "categories": Event.Category.choices,
    })


@require_safe
def event_detail(request, slug):
    event = get_object_or_404(Event, slug=slug)
    occurrences = list(event.occurrences.upcoming())
    if not occurrences:
        raise Http404("No upcoming dates for this event.")
    return render(request, "events/detail.html", {"event": event, "occurrences": occurrences})
