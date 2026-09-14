from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_handoff_documentation_covers_local_and_production_workflows():
    readme = (ROOT / "README.md").read_text()
    for text in [
        "Python 3.12",
        "uv sync",
        "manage.py migrate",
        "manage.py import_events",
        "manage.py runserver",
        "DATABASE_URL",
        "SECRET_KEY",
        "ALLOWED_HOSTS",
        "CSRF_TRUSTED_ORIGINS",
        "Vercel",
        "GitHub",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
    ]:
        assert text in readme

    example = (ROOT / ".env.example").read_text()
    for name in ["DEBUG=", "SECRET_KEY=", "DATABASE_URL=", "ALLOWED_HOSTS=", "CSRF_TRUSTED_ORIGINS="]:
        assert name in example


def test_filter_grid_css_is_rebalanced_for_the_remaining_fields():
    css = (ROOT / "events/static/events/site.css").read_text()

    # The old four-track list was sized around the search field being the widest column, and the
    # mobile selector that stretched it is dead once the field is gone. Asserting on the removed
    # tokens keeps the layout decision from silently reverting with the markup.
    assert "2fr 1.2fr 1.3fr 1.5fr" not in css
    assert "field-q" not in css
