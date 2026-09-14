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


def test_the_removed_filter_panel_leaves_no_dead_css():
    css = (ROOT / "events/static/events/site.css").read_text()

    # The panel's layout, field, action, and error rules were removed with the markup. Asserting
    # on the retired tokens keeps dead CSS from being left behind, or quietly resurrected.
    for retired in [".filter-grid", ".field", ".filter-actions", ".filter-help",
                    ".filter-errors", ".checkbox", ".filters", "field-q",
                    "2fr 1.2fr 1.3fr 1.5fr"]:
        assert retired not in css
    assert ".interest-chips" in css
    assert ".free-filter" in css
