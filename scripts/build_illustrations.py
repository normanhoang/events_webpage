#!/usr/bin/env python3
"""Build the site's local illustration assets from Lucide glyphs.

Run from the repository root:  python3 scripts/build_illustrations.py

Each asset is a 4:3 (800x600) canvas so it fills the card image box without cropping, with the
glyph centred at 12.5x.

The fill handling is the part that matters. Lucide is a *stroked* icon set: its outline paths
inherit ``fill="none"`` from the root. Better Icons instead emits ``fill="currentColor"`` on those
paths, so dropping an ink colour straight in fills every shape solid and closes the counters that
carry the icon's meaning -- the palette loses its thumb hole and paint wells, and two trees weld
into one silhouette. Restore the convention before writing the file:

* outline paths become ``fill="none"`` (never simply delete the attribute: a glyph whose paths sit
  directly under ``<svg>`` has no parent ``fill="none"`` to inherit and would paint black),
* tiny circles (r <= 1.5) stay filled, since those are Lucide's accent dots,
* larger circles are outlined.

Requires the ``better-icons`` CLI.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "events/static/events/images"

PAPER = "#eee9db"
INK = "#344d39"
ACCENT_RADIUS = 1.5
ACCENT_SIZE = 3

WRAP = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 600" role="img" aria-hidden="true">'
    f'<rect width="800" height="600" fill="{PAPER}"/>'
    '<g transform="translate(250 150) scale(12.5)">{inner}</g></svg>'
)

# Asset stem -> Lucide glyph. The event categories plus the theater-deals fallbacks.
ASSETS = {
    "art": "palette",
    "books": "book-open",
    "comics": "messages-square",
    "community": "users-round",
    "fitness": "dumbbell",
    "food": "utensils",
    "gaming": "gamepad-2",
    "music": "music",
    "other": "compass",
    "outdoors": "tree-pine",
    "queer": "heart",
    "tech": "cpu",
    "theater": "drama",
    "theater-broadway": "ticket",
    "theater-off-broadway": "drama",
    "theater-other": "music",
}


def fetch(glyph):
    icon_id = glyph if ":" in glyph else f"lucide:{glyph}"
    result = subprocess.run(["better-icons", "get", icon_id, "--size", "24"],
                            capture_output=True, text=True)
    if result.returncode or "<svg" not in result.stdout:
        raise SystemExit(f"could not fetch {icon_id}: {result.stderr.strip() or 'no output'}")
    inner = re.sub(r"^.*?<svg[^>]*>", "", result.stdout.strip(), count=1, flags=re.S)
    return re.sub(r"</svg>.*$", "", inner, count=1, flags=re.S).strip()


def is_accent(tag):
    """True when a shape is small enough to be one of Lucide's filled accent dots."""
    if tag.startswith("<circle"):
        radius = re.search(r'\br="([0-9.]+)"', tag)
        return bool(radius) and float(radius.group(1)) <= ACCENT_RADIUS
    if tag.startswith(("<rect", "<ellipse")):
        dims = [re.search(rf'\b{axis}="([0-9.]+)"', tag) for axis in ("width", "height")]
        return all(dim and float(dim.group(1)) <= ACCENT_SIZE for dim in dims)
    return False


def restore_lucide_fills(inner):
    """Strip solid fills from outline shapes, keeping Lucide's small accent dots filled.

    Applies to every outline shape type, not just <path>: Lucide's cpu icon is two <rect>
    elements, so handling only paths leaves the chip body and its inner square filled solid.
    """

    def fix(match):
        tag = match.group(0)
        if is_accent(tag):
            return re.sub(r'\s+fill="[^"]*"', f' fill="{INK}"', tag)
        return re.sub(r'\s+fill="(?!none)[^"]*"', "", tag)

    return re.sub(r"<(?:path|circle|rect|ellipse)\b[^>]*?/?>", fix, inner)


def build(glyph):
    inner = fetch(glyph).replace("currentColor", INK)
    svg = f"<!-- lucide:{glyph} -->\n" + WRAP.format(inner=restore_lucide_fills(inner)) + "\n"
    problems = []
    if 'viewBox="0 0 800 600"' not in svg:
        problems.append("canvas is not 4:3")
    if "currentColor" in svg:
        problems.append("currentColor would paint black inside an <img>")
    for tag in re.findall(r"<(?:path|circle|rect|ellipse)\b[^>]*>", svg):
        if re.search(r'width="800"', tag) or not re.search(r'fill="(?!none)', tag):
            continue  # the paper canvas, or a stroked outline
        if not is_accent(tag):
            problems.append(f"outline shape still filled solid: {tag[:60]}")
            break
    if problems:
        raise SystemExit(f"{glyph}: " + "; ".join(problems))
    return svg


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="report drift against the committed assets without writing")
    args = parser.parse_args()

    drift = []
    for stem, glyph in ASSETS.items():
        svg = build(glyph)
        path = OUT / f"{stem}.svg"
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != svg:
                drift.append(stem)
            continue
        path.write_text(svg, encoding="utf-8")
        print(f"wrote {stem}.svg <- lucide:{glyph} ({len(svg)} bytes)")

    if args.check:
        if drift:
            print("DRIFT: " + ", ".join(drift), file=sys.stderr)
            return 1
        print(f"all {len(ASSETS)} assets match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
