"""Render the ShouldIDive freediver icon at the sizes a PWA install needs.

Generates everything from a single source of truth (the silhouette path
defined here), so we don't drift between the in-app SVG mark and the
home-screen icon.

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

# Brand
BG_COLOR_HEX  = "#0369a1"   # deep-blue Excellent visibility band
FG_COLOR_HEX  = "#ffffff"   # silhouette stroke fill on the dark bg
THEME_COLOR_HEX = BG_COLOR_HEX

# All path coordinates below are in the same 24×36 viewBox the in-app
# FreediverLogo uses, so the home-screen icon and the in-app brand mark
# stay visually identical.
FREEDIVER_VIEWBOX = (24, 36)


def hex_to_rgb(hx: str) -> tuple[int, int, int]:
    hx = hx.lstrip("#")
    return tuple(int(hx[i:i+2], 16) for i in (0, 2, 4))


# Bezier sampler — PIL has no native bezier-fill, so we sample curves at
# many points and feed those into ImageDraw.polygon. With supersampling
# (rendering at 4× then LANCZOS-downsampling), the result is visually
# indistinguishable from a true vector renderer at icon sizes.
def _cubic(p0, p1, p2, p3, n: int):
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        x = u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0]
        y = u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1]
        out.append((x, y))
    return out


def render_diver_to_canvas(size: int, *, padding: float = 0.20,
                            bg_rgb=(3, 105, 161), fg_rgb=(255, 255, 255),
                            supersample: int = 4) -> Image.Image:
    """Draw the freediver silhouette onto a square canvas. Renders at
    `size * supersample` then LANCZOS-downsamples — gives smooth
    anti-aliased edges without needing cairo. Cubic-bezier paths match
    the in-app FreediverLogo SVG so the home-screen icon and the brand
    mark stay visually aligned."""
    big = size * supersample
    img = Image.new("RGBA", (big, big), bg_rgb + (255,))
    draw = ImageDraw.Draw(img)

    inset = int(big * padding)
    vb_w, vb_h = FREEDIVER_VIEWBOX
    avail_h = big - 2 * inset
    scale = avail_h / vb_h
    fig_w = vb_w * scale
    fig_h = vb_h * scale
    ox = (big - fig_w) / 2
    oy = (big - fig_h) / 2

    def p(x, y):
        return (ox + x * scale, oy + y * scale)

    N = 32  # bezier samples per segment

    # Left fin blade — same control points as the SVG path.
    left = (
        _cubic(p(12, 13), p(10.8, 9), p(9.4, 4.5), p(7.5, 1.2), N)
      + _cubic(p(7.5, 1.2), p(7.6, 4), p(9.1, 9), p(11.6, 13), N)
    )
    draw.polygon(left, fill=fg_rgb)

    # Right fin blade — mirror.
    right = (
        _cubic(p(12, 13), p(13.2, 9), p(14.6, 4.5), p(16.5, 1.2), N)
      + _cubic(p(16.5, 1.2), p(16.4, 4), p(14.9, 9), p(12.4, 13), N)
    )
    draw.polygon(right, fill=fg_rgb)

    # Body — slender torso with the slight waist taper.
    body = (
        _cubic(p(11.2, 12.5), p(10.7, 17), p(10.5, 22), p(10.9, 27), N)
      + _cubic(p(10.9, 27), p(11.0, 29), p(11.2, 30.5), p(11.5, 31.5), N)
      + [p(12.5, 31.5)]
      + _cubic(p(12.5, 31.5), p(12.8, 30.5), p(13.0, 29), p(13.1, 27), N)
      + _cubic(p(13.1, 27), p(13.5, 22), p(13.3, 17), p(12.8, 12.5), N)
    )
    draw.polygon(body, fill=fg_rgb)

    # Head — ellipse at the bottom tip.
    head_cx, head_cy = p(12, 33.5)
    head_rx = 1.7 * scale
    head_ry = 1.9 * scale
    draw.ellipse(
        [head_cx - head_rx, head_cy - head_ry,
         head_cx + head_rx, head_cy + head_ry],
        fill=fg_rgb,
    )

    if supersample > 1:
        img = img.resize((size, size), Image.LANCZOS)
    return img


def write_svg() -> None:
    """SVG master — used by modern manifests + the in-app brand mark
    stays symbolically identical (it's defined in App.jsx using the same
    path commands)."""
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" role="img" aria-label="ShouldIDive">
  <rect width="256" height="256" rx="56" fill="{BG_COLOR_HEX}"/>
  <g transform="translate(128 128) scale(5.5) translate(-12 -18)" fill="{FG_COLOR_HEX}">
    <path d="
      M 12 13
      C 10.8 9, 9.4 4.5, 7.5 1.2
      C 7.6 4, 9.1 9, 11.6 13
      Z" />
    <path d="
      M 12 13
      C 13.2 9, 14.6 4.5, 16.5 1.2
      C 16.4 4, 14.9 9, 12.4 13
      Z" />
    <path d="
      M 11.2 12.5
      C 10.7 17, 10.5 22, 10.9 27
      C 11.0 29, 11.2 30.5, 11.5 31.5
      L 12.5 31.5
      C 12.8 30.5, 13.0 29, 13.1 27
      C 13.5 22, 13.3 17, 12.8 12.5
      Z" />
    <ellipse cx="12" cy="33.5" rx="1.7" ry="1.9"/>
  </g>
</svg>
"""
    (OUT_DIR / "icon.svg").write_text(svg)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    bg_rgb = hex_to_rgb(BG_COLOR_HEX)
    fg_rgb = hex_to_rgb(FG_COLOR_HEX)

    # SVG master.
    write_svg()
    print(f"wrote {OUT_DIR / 'icon.svg'}")

    # Standard PWA install icons.
    for size in (192, 512):
        img = render_diver_to_canvas(size, padding=0.18, bg_rgb=bg_rgb, fg_rgb=fg_rgb)
        out = OUT_DIR / f"icon-{size}.png"
        img.save(out, optimize=True)
        print(f"wrote {out}")

    # Maskable variant — Android's adaptive icon clips to a circle / squircle,
    # so we add extra padding (~25%) to keep the silhouette inside the safe area.
    img = render_diver_to_canvas(512, padding=0.30, bg_rgb=bg_rgb, fg_rgb=fg_rgb)
    out = OUT_DIR / "icon-maskable-512.png"
    img.save(out, optimize=True)
    print(f"wrote {out}")

    # iOS home-screen icon (180×180, no padding so it touches edges).
    img = render_diver_to_canvas(180, padding=0.16, bg_rgb=bg_rgb, fg_rgb=fg_rgb)
    out = OUT_DIR / "apple-touch-icon.png"
    img.save(out, optimize=True)
    print(f"wrote {out}")

    # Browser-tab favicon.
    img = render_diver_to_canvas(32, padding=0.14, bg_rgb=bg_rgb, fg_rgb=fg_rgb)
    out = OUT_DIR / "favicon-32.png"
    img.save(out, optimize=True)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
