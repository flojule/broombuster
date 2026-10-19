"""Render frontend/icon.svg to the PNG icons (favicon, apple-touch, PWA).

    uv run --extra scripts python scripts/build_icons.py

The maskable icon shrinks the artwork into the central safe zone so launchers
can crop it to any shape.
"""

import re
from pathlib import Path

import cairosvg

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
SVG = (FRONTEND / "icon.svg").read_text()

# name -> (pixels, artwork scale about the centre)
OUTPUTS = {
    "favicon-32.png": (32, 1.15),
    "apple-touch-icon.png": (180, 1.0),
    "icon-192.png": (192, 1.0),
    "icon-512.png": (512, 1.0),
    "icon-maskable-512.png": (512, 0.72),
}


def render(size: int, scale: float) -> bytes:
    svg = SVG
    if scale != 1.0:
        art = re.search(r"(<g .*</g>)", svg, re.S).group(1)
        c = 256 * (1 - scale)
        wrapped = f'<g transform="translate({c:.1f} {c:.1f}) scale({scale})">{art}</g>'
        svg = svg.replace(art, wrapped)
    return cairosvg.svg2png(bytestring=svg.encode(), output_width=size, output_height=size)


for name, (size, scale) in OUTPUTS.items():
    (FRONTEND / name).write_bytes(render(size, scale))
    print("wrote", name)
