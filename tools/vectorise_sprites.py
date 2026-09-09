#!/usr/bin/env python3
"""
Trace a flat sprite silhouette into vector curves and redraw it smooth.

Most of the fleet was drawn tiny — 70x19, 80x20, 56x21 — and then blown up
six times over with nearest-neighbour, which is where the staircase edges come
from. Upscaling further only makes bigger stairs, so the fix is to recover the
shape rather than the pixels: read the art back at the size it was actually
drawn, trace that with potrace, and redraw the curves at the stored size.

This is the same treatment the bosses already had (Textures/Bosses/vectors/*.svg
next to their renders); this script is that pipeline written down so it can be
re-run.

    python tools/vectorise_sprites.py Textures/submarine_pixelart.png
    python tools/vectorise_sprites.py --all --dry-run

Only single-colour silhouettes are touched. Anything with shading (the turrets,
the oil rig, destroyer.png) is refused — tracing would flatten detail away.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

import numpy as np
import potrace
from PIL import Image, ImageDraw

# Fill each contour into its own mask and XOR it in; XOR *is* the even-odd rule,
# so holes fall out for free without tracking contour parentage.
SUPERSAMPLE = 4

# Tracing defaults, tuned on the fleet rather than inherited from potrace.
#   ALPHAMAX   potrace's corner threshold. Its 1.0 default is built for scanned
#              line art and melts a warship: masts, funnels and the stepped
#              superstructure rounded into hills. These ships are machinery, so
#              corners stay corners and only the staircases go.
#   TURDSIZE   0, not 2. At 70x19 a mast is one pixel wide and has an area of 1,
#              so anything above zero deletes it outright.
#   PRESMOOTH  the important one. Tracing the native grid directly turns a mast
#              one pixel wide into a triangular spike, and the cruisers came out
#              looking shelled. Enlarging the mask first and letting it go soft
#              at the threshold gives potrace a mast several pixels wide to fit,
#              so it stays a mast. Aim for roughly this long side before tracing.
ALPHAMAX = 0.8
TURDSIZE = 0
OPTTOLERANCE = 0.15
PRESMOOTH_TARGET = 400


def load_mask(path: str) -> tuple[np.ndarray, tuple[int, int]]:
    """Return (boolean silhouette, stored size).

    Normally the shape is the alpha channel. MLRS_Boat_preview.png has no alpha
    at all — it is a black boat on an opaque white page — so fall back to
    "anything that isn't near-white" and the sprite gains the transparency it
    should have had.
    """
    im = Image.open(path).convert("RGBA")
    a = np.array(im)
    alpha = a[..., 3]
    if (alpha < 255).any():
        return alpha > 128, im.size
    lum = a[..., :3].mean(axis=2)
    return lum < 228, im.size


def native_grid(mask: np.ndarray) -> int:
    """Largest k for which every kxk block is uniform — the size one 'pixel' of
    the original art occupies once it was scaled up."""
    h, w = mask.shape
    for k in range(12, 0, -1):
        if h % k or w % k:
            continue
        blocks = mask.reshape(h // k, k, w // k, k)
        if (blocks == blocks[:, :1, :, :1]).all():
            return k
    return 1


def _pt(p) -> tuple[float, float]:
    """potrace hands back a _Point; unpack it however this build exposes it."""
    try:
        return (p.x, p.y)
    except AttributeError:
        return tuple(p)


def flatten(curve, steps: int = 24) -> list[tuple[float, float]]:
    """Walk one potrace curve into a polyline, subdividing the cubics."""
    pts = [_pt(curve.start_point)]
    for seg in curve:
        if seg.is_corner:
            pts.append(_pt(seg.c))
            pts.append(_pt(seg.end_point))
            continue
        p0 = pts[-1]
        p1, p2, p3 = _pt(seg.c1), _pt(seg.c2), _pt(seg.end_point)
        for i in range(1, steps + 1):
            t = i / steps
            u = 1 - t
            pts.append((
                u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
                u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1],
            ))
    return pts


def to_svg_path(path, w: int, h: int) -> str:
    """One <path> in the shape of the boss vectors: cubics, even-odd, black."""
    out: list[str] = []
    for curve in path:
        x, y = _pt(curve.start_point)
        out.append(f"M{x:.2f} {y:.2f}")
        for seg in curve:
            if seg.is_corner:
                cx, cy = _pt(seg.c)
                ex, ey = _pt(seg.end_point)
                out.append(f"L{cx:.2f} {cy:.2f} L{ex:.2f} {ey:.2f}")
            else:
                a1, b1 = _pt(seg.c1)
                a2, b2 = _pt(seg.c2)
                ex, ey = _pt(seg.end_point)
                out.append(f"C{a1:.2f} {b1:.2f} {a2:.2f} {b2:.2f} {ex:.2f} {ey:.2f}")
        out.append("Z")
    d = " ".join(out)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="{w}" height="{h}">\n'
        f'  <path fill="#000000" fill-rule="evenodd" d="{d}"/>\n'
        f"</svg>\n"
    )


def render(path, src_w: int, src_h: int, out_w: int, out_h: int) -> Image.Image:
    """Redraw the traced curves at the stored size, supersampled for clean edges."""
    sx = out_w * SUPERSAMPLE / src_w
    sy = out_h * SUPERSAMPLE / src_h
    big = (out_w * SUPERSAMPLE, out_h * SUPERSAMPLE)

    acc = np.zeros((big[1], big[0]), dtype=bool)
    for curve in path:
        pts = [(x * sx, y * sy) for x, y in flatten(curve)]
        if len(pts) < 3:
            continue
        layer = Image.new("L", big, 0)
        ImageDraw.Draw(layer).polygon(pts, fill=255)
        acc ^= np.asarray(layer) > 127

    alpha = Image.fromarray((acc * 255).astype(np.uint8), "L").resize(
        (out_w, out_h), Image.LANCZOS)
    out = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
    out.putalpha(alpha)
    return out


def vectorise(path_png: str, *, dry_run: bool = False, force: bool = False,
              alphamax: float = ALPHAMAX) -> str:
    mask, (w, h) = load_mask(path_png)

    im = Image.open(path_png).convert("RGBA")
    a = np.array(im)
    opaque = a[a[..., 3] > 128][:, :3]
    if len(opaque) and len(np.unique(opaque.reshape(-1, 3), axis=0)) > 3 and not force:
        return f"skip  {os.path.basename(path_png):<32} shaded art — tracing would flatten it"

    k = native_grid(mask)
    native = mask[::k, ::k] if k > 1 else mask

    nh0, nw0 = native.shape
    scale = max(1, min(4, round(PRESMOOTH_TARGET / max(nw0, nh0))))
    if scale > 1:
        soft = Image.fromarray((native * 255).astype(np.uint8), "L").resize(
            (nw0 * scale, nh0 * scale), Image.BILINEAR)
        native = np.asarray(soft) > 127

    # Two traps here, both silent.
    #   dtype:    a uint8 array of 0/1 is read against potrace's own threshold and
    #             every pixel lands the same side of it, collapsing the sprite to
    #             one four-segment blob. It has to stay boolean.
    #   polarity: this build treats True as background, so the mask goes in
    #             inverted; feed it the right way up and you trace the sea
    #             around the ship and get a perfect negative.
    traced = potrace.Bitmap(~native).trace(
        turdsize=TURDSIZE, turnpolicy=potrace.POTRACE_TURNPOLICY_MAJORITY,
        alphamax=alphamax, opticurve=True, opttolerance=OPTTOLERANCE)

    nh, nw = native.shape
    if dry_run:
        return (f"would  {os.path.basename(path_png):<32} grid {k}x  "
                f"native {nw0}x{nh0} x{scale} -> trace {nw}x{nh} -> redraw {w}x{h}")

    # Keep the untouched original the way the bosses do, so a trace that goes
    # wrong is one file-copy away from being undone.
    backup_dir = os.path.join(os.path.dirname(path_png), "_original_backup")
    os.makedirs(backup_dir, exist_ok=True)
    backup = os.path.join(backup_dir, os.path.basename(path_png))
    if not os.path.exists(backup):
        shutil.copy2(path_png, backup)

    svg_dir = os.path.join(os.path.dirname(path_png), "vectors")
    os.makedirs(svg_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(path_png))[0]
    with open(os.path.join(svg_dir, stem + ".svg"), "w", encoding="utf-8") as fh:
        fh.write(to_svg_path(traced, nw, nh))

    render(traced, nw, nh, w, h).save(path_png)
    return (f"done  {os.path.basename(path_png):<32} grid {k}x  "
            f"native {nw0}x{nh0} x{scale} -> {w}x{h}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", help="PNGs to trace")
    ap.add_argument("--all", action="store_true", help="every flat sprite in Textures/")
    ap.add_argument("--dry-run", action="store_true", help="report, change nothing")
    ap.add_argument("--force", action="store_true", help="trace even if shaded")
    ap.add_argument("--alphamax", type=float, default=ALPHAMAX,
                    help=f"corner smoothing, 0 = keep every corner (default {ALPHAMAX})")
    args = ap.parse_args()

    files = list(args.files)
    if args.all:
        import glob
        files += sorted(glob.glob("Textures/*.png"))
    if not files:
        ap.error("give it some files, or --all")

    for f in files:
        print(vectorise(f, dry_run=args.dry_run, force=args.force,
                        alphamax=args.alphamax))
    return 0


if __name__ == "__main__":
    sys.exit(main())
