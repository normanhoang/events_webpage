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


def test_all_category_art_uses_local_lucide_svgs_and_templates_have_no_unicode_icons():
    image_dir = ROOT / "events/static/events/images"
    categories = {
        "art", "books", "comics", "community", "fitness", "food", "gaming", "music",
        "other", "outdoors", "queer", "tech", "theater",
    }
    for category in categories:
        svg = (image_dir / f"{category}.svg").read_text()
        assert "lucide" in svg
        assert "<svg" in svg
        # 4:3 to match the card image box, and explicit ink rather than currentColor:
        # these render inside an <img>, where currentColor cannot inherit from the page.
        assert 'viewBox="0 0 800 600"' in svg
        assert "currentColor" not in svg

    for template in (ROOT / "events/templates/events").glob("*.html"):
        text = template.read_text()
        for legacy_glyph in ("↗", "←", "✳"):
            assert legacy_glyph not in text, template.name


ILLUSTRATION_ASSETS = [
    "art", "books", "comics", "community", "fitness", "food", "gaming", "music", "other",
    "outdoors", "queer", "tech", "theater",
    "theater-broadway", "theater-off-broadway", "theater-other",
]


def test_illustration_art_keeps_lucide_outlines_stroked_instead_of_filled_solid():
    import re

    # Better Icons marks Lucide's outline paths fill="currentColor", so dropping an ink colour
    # straight in fills every shape solid and closes the counters that carry the icon's meaning:
    # the palette lost its thumb hole and paint wells, and two trees welded into one silhouette.
    # Only Lucide's tiny accent circles (r <= 1.5) may be filled.
    image_dir = ROOT / "events/static/events/images"

    for stem in ILLUSTRATION_ASSETS:
        svg = (image_dir / f"{stem}.svg").read_text()
        for tag in re.findall(r"<(?:path|circle|rect|ellipse)\b[^>]*>", svg):
            if 'width="800"' in tag:
                continue  # the paper canvas
            if not re.search(r'fill="(?!none)', tag):
                continue  # a stroked outline
            # Only an accent dot may be filled. Checking every shape type matters: Lucide's cpu
            # icon is two <rect> elements, so a path-only check passes while the chip body and its
            # inner square stay solid — which is exactly how that icon shipped as a blob.
            if tag.startswith("<circle"):
                radius = re.search(r'\br="([0-9.]+)"', tag)
                assert radius and float(radius.group(1)) <= 1.5, f"{stem}: filled circle is not an accent"
            else:
                raise AssertionError(f"{stem}: outline shape filled solid: {tag[:70]}")


def test_illustration_assets_match_the_glyph_declared_by_the_builder():
    import importlib.util
    import re

    # The builder is the single source of truth for which glyph each asset came from. Without this
    # check a hand-edited or stale asset keeps its old artwork while the builder claims otherwise.
    spec = importlib.util.spec_from_file_location(
        "build_illustrations", ROOT / "scripts/build_illustrations.py"
    )
    assert spec and spec.loader
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)

    image_dir = ROOT / "events/static/events/images"
    assert set(builder.ASSETS) == set(ILLUSTRATION_ASSETS)
    for stem, glyph in builder.ASSETS.items():
        svg = (image_dir / f"{stem}.svg").read_text()
        declared = re.match(r"\s*<!--\s*lucide:([a-z0-9-]+)\s*-->", svg)
        assert declared, f"{stem}: missing lucide provenance comment"
        assert declared.group(1) == glyph, f"{stem}: built from {declared.group(1)}, declared {glyph}"


def test_theater_grid_shows_three_cards_across_on_desktop_like_the_events_grid():
    css = (ROOT / "events/static/events/site.css").read_text()

    assert ".theater-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr))" in css
    # Cards must hug their content: a one-offer card stretched to match a two-offer neighbour
    # shows a large empty void inside its border.
    assert "align-items:start}" in css.split(".theater-grid{")[1].split("}")[0]
    # Three across needs the same intermediate step the events grid uses, or the cards get
    # cramped between the desktop width and the single-column breakpoint.
    assert ("@media(min-width:701px) and (max-width:1050px){.theater-grid{"
            "grid-template-columns:repeat(2,minmax(0,1fr))}}") in css
    # And it must still collapse to one column on a phone.
    assert "@media(max-width:700px){.theater-hero{padding-block:36px 16px}.theater-grid{" in css


def test_every_referenced_ui_icon_exists_on_disk():
    import re

    icon_dir = ROOT / "events/static/events/icons"
    referenced = set()
    for template in (ROOT / "events/templates/events").glob("*.html"):
        referenced.update(re.findall(r'icon="([A-Za-z0-9_-]+)"', template.read_text()))

    assert referenced
    for name in sorted(referenced):
        # Production uses CompressedManifestStaticFilesStorage, where a missing entry raises at
        # render time — and base.html includes an icon, so one bad name would 500 every page.
        assert (icon_dir / f"{name}.svg").is_file(), f"missing icon asset: {name}"

    # The existence check above only sees literal names, so a dynamic reference would silently
    # skip it — and it would also put template data into a CSS url() inside a style attribute.
    for template in (ROOT / "events/templates/events").glob("*.html"):
        assert 'icon="{{' not in template.read_text(), template.name
