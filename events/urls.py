from django.urls import path

from . import views

urlpatterns = [
    path("health/", views.health, name="health"),
    path("theater-deals/", views.theater_deals, name="theater_deals"),
    path("", views.home, name="home"),
    path("events/<slug:slug>/", views.event_detail, name="event_detail"),
]
