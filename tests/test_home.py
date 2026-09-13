import pytest


@pytest.mark.django_db
def test_homepage_introduces_normans_personalized_feed(client):
    response = client.get("/")

    assert response.status_code == 200
    assert b"Norman\xe2\x80\x99s NYC Events" in response.content
