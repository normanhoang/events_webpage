from django.contrib import admin

from .models import Event, Occurrence


class OccurrenceInline(admin.TabularInline):
    model = Occurrence
    extra = 0
    readonly_fields = ("created_at", "updated_at")


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "neighborhood", "borough", "top_pick", "verified_at", "updated_at")
    list_filter = ("category", "borough", "top_pick", "is_active", "verified_at")
    search_fields = ("title", "official_url", "venue", "neighborhood", "description")
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("created_at", "updated_at")
    inlines = (OccurrenceInline,)
    fieldsets = (
        (None, {"fields": ("title", "slug", "description", "category", "tags")}),
        ("Location & source", {"fields": ("venue", "neighborhood", "borough", "official_url", "image_url")}),
        ("Pricing", {"fields": ("price_label", "price_min", "price_max", "currency")}),
        ("Editorial", {"fields": ("fit_reason", "top_pick", "verified_at")}),
        ("History", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(Occurrence)
class OccurrenceAdmin(admin.ModelAdmin):
    list_display = ("event", "source_key", "starts_at", "ends_at")
    list_filter = ("starts_at", "event__category")
    search_fields = ("event__title", "event__official_url", "source_key")
    autocomplete_fields = ("event",)
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "starts_at"
    list_select_related = ("event",)


admin.site.site_header = "Norman’s Events · Editorial"
admin.site.site_title = "Norman’s Events"
admin.site.index_title = "Manage the shortlist"
