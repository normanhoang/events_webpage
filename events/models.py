from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone


class Event(models.Model):
    class Category(models.TextChoices):
        ART = "art", "Art & culture"
        THEATER = "theater", "Broadway & theater"
        QUEER = "queer", "Queer NYC"
        TECH = "tech", "Tech, AI & finance"
        COMICS = "comics", "Comics, manga & anime"
        GAMING = "gaming", "Gaming"
        BOOKS = "books", "Books & fiction"
        FITNESS = "fitness", "Fitness"
        MUSIC = "music", "Music"
        FOOD = "food", "Food & drink"
        OUTDOORS = "outdoors", "Outdoors"
        COMMUNITY = "community", "Community"
        OTHER = "other", "Something different"

    title = models.CharField(max_length=240)
    slug = models.SlugField(max_length=260, unique=True)
    description = models.TextField()
    category = models.CharField(max_length=24, choices=Category.choices, default=Category.OTHER)
    tags = models.JSONField(default=list, blank=True)
    venue = models.CharField(max_length=240, blank=True)
    neighborhood = models.CharField(max_length=120, blank=True)
    borough = models.CharField(max_length=120, blank=True)
    official_url = models.URLField(max_length=1000, unique=True)
    image_url = models.URLField(max_length=1000, blank=True)
    price_label = models.CharField(max_length=120, blank=True)
    price_min = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    price_max = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="USD")
    fit_reason = models.TextField(blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    top_pick = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(price_min__gte=0) | models.Q(price_min__isnull=True), name="event_min_nonnegative"),
            models.CheckConstraint(condition=models.Q(price_max__gte=0) | models.Q(price_max__isnull=True), name="event_max_nonnegative"),
            models.CheckConstraint(condition=models.Q(price_max__gte=models.F("price_min")) | models.Q(price_min__isnull=True) | models.Q(price_max__isnull=True), name="event_price_order"),
        ]

    def __str__(self):
        return self.title

    def clean(self):
        super().clean()
        errors = {}
        if not isinstance(self.tags, list) or any(not isinstance(tag, str) or not tag.strip() for tag in self.tags):
            errors["tags"] = "Tags must be a list of nonempty strings."
        if len(self.currency) != 3 or not self.currency.isascii() or not self.currency.isalpha() or not self.currency.isupper():
            errors["currency"] = "Use a three-letter uppercase currency code."
        for field in ("official_url", "image_url"):
            if value := getattr(self, field):
                try:
                    URLValidator(schemes=["http", "https"])(value)
                except ValidationError:
                    errors[field] = "Use a complete http or https URL."
        if errors:
            raise ValidationError(errors)

    @property
    def fallback_image(self):
        category = self.category if self.category in self.Category.values else "other"
        return f"events/images/{category}.svg"

    def get_absolute_url(self):
        return reverse("event_detail", kwargs={"slug": self.slug})


class OccurrenceQuerySet(models.QuerySet):
    def upcoming(self, at=None):
        at = at or timezone.now()
        return self.filter(is_active=True, event__is_active=True).filter(
            models.Q(ends_at__gt=at) | models.Q(ends_at__isnull=True, starts_at__gte=at)
        )


class Occurrence(models.Model):
    objects = OccurrenceQuerySet.as_manager()

    event = models.ForeignKey(Event, related_name="occurrences", on_delete=models.CASCADE)
    source_key = models.CharField(max_length=240)
    starts_at = models.DateTimeField(db_index=True)
    ends_at = models.DateTimeField(null=True, blank=True, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["starts_at", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["event", "source_key"], name="occurrence_source_unique"),
            models.CheckConstraint(condition=models.Q(ends_at__gte=models.F("starts_at")) | models.Q(ends_at__isnull=True), name="occurrence_time_order"),
        ]

    def __str__(self):
        return f"{self.event} — {self.starts_at:%Y-%m-%d %H:%M}"
