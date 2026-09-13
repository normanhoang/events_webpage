from django.urls import path

from .views import event_detail, home

urlpatterns = [
    path("", home, name="home"),
    path("events/<slug:slug>/", event_detail, name="event_detail"),
]
