from django.urls import path

from . import views

urlpatterns = [
    path("health/", views.health, name="health"),
    path("", views.home, name="home"),
    path("events/<slug:slug>/", views.event_detail, name="event_detail"),
]
