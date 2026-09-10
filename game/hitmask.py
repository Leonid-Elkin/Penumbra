"""
game.hitmask – per-sprite collision silhouettes.

A ship sprite's bounding box is mostly air: tall thin masts poke up out of the
hull and there is empty sky in the corners. Colliding shells against that whole
box means rounds "hit" a mast or blank space. A HitMask fixes that by carrying,
for each vertical slice of the sprite, the band of rows that are actually solid
HULL + SUPERSTRUCTURE – so a projectile only lands when it strikes real plating,
and flies right through masts and empty space.

The mask is built once per unit from its (cropped, recoloured) source image and
cached on the ShipDef. Collision code asks `contains(u, v)` with sprite-relative
coordinates in 0..1.
"""

from __future__ import annotations


class HitMask:
    """A columnar silhouette: for each of `cols` vertical slices, the normalized
    [top, bottom] row band that counts as a hit (or None where the slice is empty).
    """
    __slots__ = ("cols", "top", "bot", "_com")

    def __init__(self, cols: int, top: list, bot: list):
        self.cols = cols
        self.top  = top          # list[float | None], normalized 0..1 (0 = sprite top)
        self.bot  = bot          # list[float | None]
        self._com = None         # cached (u, v) centre of mass, computed on demand

    def centroid(self) -> tuple:
        """(u, v) centre of mass of the SOLID silhouette, sprite-relative 0..1.

        The sprite bounding box is mostly empty – a surface ship rides high in its
        frame (only the lower hull is underwater) and a sub's art carries clear
        water above and below the boat. So the geometric middle of the sprite
        (`mid_y`) is usually NOT on solid plating. Homing rounds should aim HERE,
        at the mass of the real hitbox, not at the frame centre. Weighted by each
        column's solid band thickness. Falls back to (0.5, 0.5) if empty."""
        if self._com is None:
            wsum = uacc = vacc = 0.0
            for i in range(self.cols):
                t = self.top[i]; b = self.bot[i]
                if t is None or b is None:
                    continue
                h = b - t                          # column's solid thickness (weight)
                if h <= 0:
                    continue
                u = (i + 0.5) / self.cols
                v = (t + b) * 0.5
                wsum += h; uacc += u * h; vacc += v * h
            self._com = (uacc / wsum, vacc / wsum) if wsum > 0 else (0.5, 0.5)
        return self._com

    def contains(self, u: float, v: float) -> bool:
        """True if sprite-relative point (u across, v down), each in 0..1, lands on
        solid hull/superstructure."""
        if u < 0.0 or u >= 1.0 or v < 0.0 or v > 1.0:
            return False
        i = int(u * self.cols)
        if i < 0: i = 0
        elif i >= self.cols: i = self.cols - 1
        t = self.top[i]
        if t is None:
            return False
        return t <= v <= self.bot[i]

    # ── Construction ──────────────────────────────────────────────────────────
    @classmethod
    def from_rect(cls, x0: float, y0: float, x1: float, y1: float,
                  cols: int = 48) -> "HitMask":
        """A plain rectangular hitbox (JSON `hitbox` override), all four edges as
        sprite fractions. Handy when a unit's silhouette isn't the shape you want
        shells to respect."""
        x0, x1 = sorted((max(0.0, x0), min(1.0, x1)))
        y0, y1 = sorted((max(0.0, y0), min(1.0, y1)))
        top = [None] * cols
        bot = [None] * cols
        for i in range(cols):
            c = (i + 0.5) / cols
            if x0 <= c <= x1:
                top[i] = y0; bot[i] = y1
        return cls(cols, top, bot)

    @classmethod
    def from_image(cls, pil_rgba, cols: int = 72, alpha: int = 60) -> "HitMask | None":
        """Derive a silhouette from a Pillow RGBA image.

        Steps: downsample to `cols` slices wide; mark pixels with alpha above the
        threshold as solid; then TRIM tall thin masts by cutting away the top rows
        whose fill (opaque fraction of the ship's width) is mast-thin, keeping the
        dense hull + superstructure. Each column's hit band is the solid span that
        survives, so empty sky columns fore/aft/above simply become no-hit.

        Returns None if the image is empty or too degenerate to trust (caller then
        falls back to a simple box)."""
        w, h = pil_rgba.size
        if w <= 0 or h <= 0:
            return None
        rows = max(1, round(cols * h / w))
        small = pil_rgba.convert("RGBA").resize((cols, rows))
        px = small.load()

        solid = [[px[x, y][3] > alpha for x in range(cols)] for y in range(rows)]
        col_has = [any(solid[y][x] for y in range(rows)) for x in range(cols)]
        row_fill = [sum(1 for x in range(cols) if solid[y][x]) for y in range(rows)]

        xs = [x for x in range(cols) if col_has[x]]
        opaque_rows = [y for y in range(rows) if row_fill[y] > 0]
        if not xs or not opaque_rows:
            return None
        width_px = (max(xs) - min(xs)) + 1

        # A row belongs to the hull/superstructure once a real fraction of the
        # ship's width is filled; sparse rows above that are masts/rigging.
        structural_min = max(2, round(0.06 * width_px))
        structural = [y for y in opaque_rows if row_fill[y] >= structural_min]
        body_top = min(structural) if structural else min(opaque_rows)
        body_bot = max(opaque_rows)

        # Per-column solid span, clipped to the body band (drops the mast tops).
        top = [None] * cols
        bot = [None] * cols
        for x in range(cols):
            ys = [y for y in range(body_top, body_bot + 1) if solid[y][x]]
            if ys:
                top[x] = ys[0]
                bot[x] = ys[-1] + 1        # +1 so a 1-row slice has real thickness

        if not any(t is not None for t in top):
            return None

        # Slight dilation so the very hull edge and thin one-slice gaps still read
        # as a hit (collision should feel solid, never porous).
        pad_y = 1
        d_top = list(top); d_bot = list(bot)
        for x in range(cols):
            if top[x] is not None:
                d_top[x] = max(0, top[x] - pad_y)
                d_bot[x] = min(rows, bot[x] + pad_y)
            else:
                l = top[x - 1] if x > 0 else None
                r = top[x + 1] if x < cols - 1 else None
                if l is not None and r is not None:      # bridge a lone empty slice
                    d_top[x] = max(0, min(l, top[x + 1]) - pad_y)
                    d_bot[x] = min(rows, max(bot[x - 1], bot[x + 1]) + pad_y)

        inv = 1.0 / rows
        ntop = [None if t is None else t * inv for t in d_top]
        nbot = [None if b is None else b * inv for b in d_bot]
        return cls(cols, ntop, nbot)
