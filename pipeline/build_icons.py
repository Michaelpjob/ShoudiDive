"""Render the ShouldIDive dive-flag icon at the sizes a PWA install needs.

The icon mirrors the universal "diver below" maritime flag — a red
square with a white stripe running corner-to-corner. Reads instantly
at every size, including the 16 px favicon where the previous
freediver silhouette degraded into a fuzzy upside-down Y.

Outputs in `public/`:
  icon.svg                — vector master (used by modern manifests)
  icon-192.png            — Android PWA install size
  icon-512.png            — Android PWA install size + maskable target
  apple-touch-icon.png    — iOS home-screen icon (180×180)
  icon-maskable-512.png   — Android maskable variant (safe-area padded)
  favicon-32.png          — browser tab favicon

Run on demand (ship the PNGs to the repo; no need for a runtime build):
  python pipeline/build_icons.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "public"

# Brand — universal diver-below flag colours.
RED_HEX     = "#dc2626"
WHITE_HEX   = "#ffffff"
THEME_COLOR_HEX = RED_HEX  # PWA theme/status bar tint


def hex_to_rgb(hx: str) -> tuple[int, int, int]:
    hx = hx.lstrip("#")
    return tuple(int(hx[i:i+2], 16) for i in (0, 2, 4))


def render_flag_to_canvas(size: int, *, padding: float = 0.06,
                           radius_pct: float = 0.16,
                           supersample: int = 4) -> Image.Image:
    """Draw the dive flag onto a square canvas. Renders at
    `size * supersample` then LANCZOS-downsamples for clean edges
    without needing cairo. The square has rounded corners (radius =
    radius_pct of side length); the stripe runs from upper-right to
    lower-left at ~24% of the side width."""
    big = size * supersample
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    red   = hex_to_rgb(RED_HEX)
    white = hex_to_rgb(WHITE_HEX)

    inset = int(round(big * padding))
    sq_l, sq_t = inset, inset
    sq_r, sq_b = big - inset, big - inset
    radius = int(round((sq_r - sq_l) * radius_pct))
    draw.rounded_rectangle(
        [sq_l, sq_t, sq_r, sq_b],
        radius=radius,
        fill=red + (255,),
    )

    # White stripe — corner-to-corner, ~22% of square side wide.
    stripe_w = int(round((sq_r - sq_l) * 0.22))
    # Cap the stripe inside the rounded corners with a generous inset so
    # it doesn't poke past the rounded edges at small sizes.
    cap_inset = int(round((sq_r - sq_l) * 0.10))
    p_top_right    = (sq_r - cap_inset, sq_t + cap_inset)
    p_bottom_left  = (sq_l + cap_inset, sq_b - cap_inset)
    draw.line(
        [p_top_right, p_bottom_left],
        fill=white + (255,),
        width=stripe_w,
        joint="curve",
    )
    # Cap the line ends with circles so they read as a clean stripe
    # instead of square stamps at small sizes.
    half = stripe_w // 2
    for cx, cy in (p_top_right, p_bottom_left):
        draw.ellipse(
            [cx - half, cy - half, cx + half, cy + half],
            fill=white + (255,),
        )

    if supersample > 1:
        img = img.resize((size, size), Image.LANCZOS)
    return img


def write_svg() -> None:
    """SVG master — kept symbolically identical to the in-app
    FreediverLogo (App.jsx) so the home-screen icon and the brand mark
    render the same dive flag at every scale."""
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" role="img" aria-label="ShouldIDive">
  <rect x="20" y="20" width="216" height="216" rx="42" fill="{RED_HEX}"/>
  <line x1="216" y1="40" x2="40" y2="216" stroke="{WHITE_HEX}" stroke-width="42" stroke-linecap="round"/>
</svg>
"""
    (OUT_DIR / "icon.svg").write_text(svg)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # SVG master.
    write_svg()
    print(f"wrote {OUT_DIR / 'icon.svg'}")

    # Standard PWA install icons.
    for size in (192, 512):
        img = render_flag_to_canvas(size, padding=0.06)
        out = OUT_DIR / f"icon-{size}.png"
        img.save(out, optimize=True)
        print(f"wrote {out}")

    # Maskable variant — Android's adaptive icon clips to a circle /
    # squircle, so we keep more of the flag inside the safe area
    # (extra outer padding, larger corner radius).
    img = render_flag_to_canvas(512, padding=0.18, radius_pct=0.20)
    out = OUT_DIR / "icon-maskable-512.png"
    img.save(out, optimize=True)
    print(f"wrote {out}")

    # iOS home-screen icon (180×180, slim padding so the flag fills).
    img = render_flag_to_canvas(180, padding=0.06)
    out = OUT_DIR / "apple-touch-icon.png"
    img.save(out, optimize=True)
    print(f"wrote {out}")

    # Browser-tab favicon — needs to be readable at 16 px in a tab
    # listing, so corners stay sharp and the stripe is a hair wider.
    img = render_flag_to_canvas(32, padding=0.04, radius_pct=0.18)
    out = OUT_DIR / "favicon-32.png"
    img.save(out, optimize=True)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
