"""
game.ui.tutorial — a practical, in-battle "first run" guide.

Instead of a wall of text, this is an overlay drawn over the battle the first time
a new profile plays: it pauses the action, dims everything EXCEPT the real HUD
sections (which it spotlights and labels), and shows a graphical air / surface /
underwater interaction chart using the actual unit sprites. Dismiss it with the
button to start fighting; re-arm it from the campaign's "HOW TO PLAY" button.
"""

from __future__ import annotations
from PyQt6.QtWidgets import QWidget, QPushButton
from PyQt6.QtCore import Qt, QPoint, QRect, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QRegion, QFontMetrics

from . import theme

# Domain panels: (representative unit key, title, line, counter-rule).
_DOMAINS = [
    ("torpedo_bomber", "#6aa3c4", "AIR",        "Aircraft fly over the sea.",
     "Only ANTI-AIR units can hit them."),
    ("patrol",         "#2a6f97", "SURFACE",    "Ships fight on the waterline.",
     "The backbone: hit by most guns."),
    ("submarine",      "#15384f", "UNDERWATER", "Submarines hide below.",
     "Only DEPTH CHARGES / TORPEDOES reach them."),
]


class TutorialOverlay(QWidget):
    """A spotlight overlay over a running battle. Pauses the game while shown."""

    def __init__(self, parent: QWidget, hud, on_exit=None):
        super().__init__(parent)
        self.hud = hud
        self.canvas = getattr(hud, 'canvas', None)
        self.sprites = getattr(self.canvas, 'sprites', None)
        # When opened from the campaign's HOW TO PLAY button there is no battle to
        # fight — the underlying battle only exists to give the guide a live HUD to
        # point at — so the dismiss button leaves instead of starting the fight.
        self._on_exit = on_exit
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setGeometry(parent.rect())
        parent.installEventFilter(self)

        if self.canvas is not None:
            self.canvas._timer.stop()                       # pause while the guide is up
            # Settle the fort guns into a calm resting pose so nothing appears to
            # fire or swivel in the still frame behind the guide.
            if hasattr(self.canvas, 'set_tutorial_freeze'):
                self.canvas.set_tutorial_freeze(True)

        self.btn = QPushButton("RETURN TO MENU" if on_exit else "START BATTLE  ✓", self)
        self.btn.setStyleSheet(theme.BTN_PRIMARY)
        self.btn.setFixedSize(230, 46)
        self.btn.clicked.connect(self._dismiss)
        self._place_button()
        self.raise_(); self.show()

    # ── lifecycle ────────────────────────────────────────────────────────────
    def eventFilter(self, obj, ev):
        if ev.type() == ev.Type.Resize:
            self.setGeometry(obj.rect()); self._place_button()
        return False

    def _place_button(self):
        self.btn.move(self.width() // 2 - self.btn.width() // 2, 14)

    def _dismiss(self):
        if self._on_exit is not None:                       # HOW TO PLAY: leave, don't fight
            cb = self._on_exit
            self.deleteLater()
            cb()
            return
        if self.canvas is not None:
            if hasattr(self.canvas, 'set_tutorial_freeze'):
                self.canvas.set_tutorial_freeze(False)      # let the guns fight again
            self.canvas._prev_ms = None                     # avoid a dt spike on resume
            self.canvas._timer.start(16)
            self.canvas.setFocus()
        self.deleteLater()

    # ── HUD section targets ──────────────────────────────────────────────────
    def _targets(self):
        h = self.hud
        vals = lambda d: list(getattr(h, d, {}).values())
        res = [w for w in (getattr(h, 'gauge', None),) if w is not None]
        return [
            (vals('unit_btns'),    "DEPLOY UNITS",
             "Click / hotkey to build. Press again while cooling down to queue (max 10)."),
            (vals('upg_cards'),    "UPGRADE BASE",
             "Income · Factory (faster cooldowns) · Storage · Armor · Salvage. They take time to research."),
            (res,                  "RESOURCES",
             "Earn income each second; spend it to build. Grow it with upgrades."),
            ([getattr(h, 'minimap', None)], "MINIMAP",
             "The whole battle line. Click it to jump the camera."),
        ]

    def _section_rect(self, widgets):
        rect = None
        for w in widgets:
            if w is None or not w.isVisible():
                continue
            tl = self.mapFromGlobal(w.mapToGlobal(QPoint(0, 0)))
            r = QRect(tl, w.size())
            rect = r if rect is None else rect.united(r)
        return rect

    # ── paint ────────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W = self.width()

        sections = []
        for widgets, label, desc in self._targets():
            r = self._section_rect(widgets)
            if r is not None and r.width() > 0:
                sections.append((r, label, desc))

        # Dim EVERYTHING except the spotlighted HUD sections (which stay bright so
        # the player can see what each label points at).
        region = QRegion(self.rect())
        for r, _, _ in sections:
            region = region.subtracted(QRegion(r.adjusted(-3, -3, 3, 3)))
        p.save(); p.setClipRegion(region)
        p.fillRect(self.rect(), QColor(6, 9, 12, 210))
        p.restore()

        p.setPen(QColor(theme.ACCENT)); p.setFont(theme.head(22, 4))
        p.drawText(0, 74, W, 30, Qt.AlignmentFlag.AlignHCenter, "CAMPAIGN GUIDE")
        p.setPen(QColor(theme.TEXT_DIM)); p.setFont(theme.font(10))
        p.drawText(0, 104, W, 18, Qt.AlignmentFlag.AlignHCenter,
                   "Destroy the RED fort")

        # Two fixed briefing bands, centred and reserved so nothing else can land on
        # them: the unit-type chart (what the enemy fields) and, below it, notes on
        # your own fort (what the base and its coastal guns are).
        domain_rect = self._draw_domains(p, W)
        fort_rect = self._draw_fort_info(p, W, domain_rect.bottom() + 14)

        # HUD callouts are sized to their text, then placed so they never overlap
        # each other OR the reserved bands above: each label tries above/below/beside
        # its target and is bumped clear of anything already down, so no two pieces
        # of text can ever collide, at any window size.
        hw = min(W, 620)                        # reserve just the centred title text,
        placed = [QRect((W - hw) // 2, 60, hw, 62)]   # leaving the corners free for callouts
        placed.append(domain_rect)              # reserve the AIR/SURFACE/UNDERWATER chart
        placed.append(fort_rect)                # reserve the BASE / COASTAL GUNS notes
        for r, label, desc in sections:
            self._draw_callout(p, r, label, desc, placed)

    def _draw_fort_info(self, p, W, top):
        """Two side-by-side notes on the player's own fort — what the base is and
        what its coastal guns do — each box sized to its wrapped text so nothing is
        ever clipped. Returns the block's rect so callouts stay clear of it."""
        items = [
            ("YOUR BASE",
             "The fort on the left. It earns income every second, builds your whole "
             "fleet and mounts your guns. Dont let it be destroyed"),
            ("COASTAL GUNS",
             "Fixed artillery built onto the fort. They shell the enemy line on "
             "their own; set how far they lob with the ↑ / ↓ keys. Unlock new mounted weapons over time."),
        ]
        gap = 28
        bw = min(430, (W - 140 - gap) // 2)
        total = 2 * bw + gap
        x0 = (W - total) // 2
        wrap = int(Qt.AlignmentFlag.AlignLeft) | int(Qt.TextFlag.TextWordWrap)
        p.setFont(theme.font(10))
        th = max(p.fontMetrics().boundingRect(QRect(0, 0, bw - 24, 400), wrap, d).height()
                 for _, d in items)
        h = 28 + th + 12
        for i, (label, desc) in enumerate(items):
            x = x0 + i * (bw + gap)
            r = QRect(x, top, bw, h)
            p.fillRect(r, QColor(theme.PANEL))
            p.setPen(QPen(QColor(theme.LINE), 1)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r)
            p.setPen(QColor(theme.ACCENT)); p.setFont(theme.head(12, 2))
            p.drawText(r.adjusted(12, 7, -12, 0), Qt.AlignmentFlag.AlignLeft, label)
            p.setPen(QColor(theme.TEXT)); p.setFont(theme.font(10))
            p.drawText(r.adjusted(12, 28, -12, -8), wrap, desc)
        return QRect(x0, top, total, h)

    def _draw_domains(self, p, W):
        n = len(_DOMAINS)
        cw = min(290, (W - 140) // n); gap = 28
        total = n * cw + (n - 1) * gap
        x0 = (W - total) // 2; y0 = 132; h = 200
        chart_rect = QRect(x0, y0, total, h)
        for i, (key, tint, name, line1, line2) in enumerate(_DOMAINS):
            x = x0 + i * (cw + gap)
            r = QRect(x, y0, cw, h)
            p.fillRect(r, QColor(theme.PANEL))
            p.setPen(QPen(QColor(theme.LINE), 1)); p.setBrush(Qt.BrushStyle.NoBrush); p.drawRect(r)
            # sprite icon on a domain-tinted plate (player hulls are near-black, so
            # a lighter sea/sky backdrop makes them read clearly)
            plate = QRect(x + cw // 2 - 52, y0 + 14, 104, 52)
            p.fillRect(plate, QColor(tint))
            spr = self.sprites.get(f"player_{key}") if self.sprites else None
            if spr is not None and not spr.isNull():
                sc = spr.scaled(plate.width() - 12, plate.height() - 10,
                                Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation)
                p.drawPixmap(plate.x() + (plate.width() - sc.width()) // 2,
                             plate.y() + (plate.height() - sc.height()) // 2, sc)
            p.setPen(QColor(theme.ACCENT)); p.setFont(theme.head(14, 3))
            p.drawText(x, y0 + 80, cw, 24, Qt.AlignmentFlag.AlignHCenter, name)
            p.setPen(QColor(theme.TEXT)); p.setFont(theme.font(10))
            p.drawText(QRect(x + 12, y0 + 110, cw - 24, 34),
                       int(Qt.AlignmentFlag.AlignHCenter) | int(Qt.TextFlag.TextWordWrap), line1)
            p.setPen(QColor(theme.GOLD)); p.setFont(theme.font(10, True))
            p.drawText(QRect(x + 12, y0 + 146, cw - 24, 46),
                       int(Qt.AlignmentFlag.AlignHCenter) | int(Qt.TextFlag.TextWordWrap), line2)
        return chart_rect

    def _place_box(self, lw, lh, target, placed):
        """Find a slot for a callout box: try just above its target, then below,
        then to either side, and fall back to stacking clear above everything it
        would touch. The chosen box never overlaps anything already in `placed`,
        so no two callouts (nor a callout and the header / chart) share pixels."""
        W, H = self.width(), self.height()
        clampx = lambda x: min(max(x, 6), W - lw - 6)
        clampy = lambda y: min(max(y, 6), H - lh - 6)
        cx = clampx(target.center().x() - lw // 2)
        cy = clampy(target.center().y() - lh // 2)

        def clear(box):
            if box.top() < 6 or box.bottom() > H - 6 or box.left() < 6 or box.right() > W - 6:
                return False
            return not any(box.intersects(pb.adjusted(-6, -6, 6, 6)) for pb in placed)

        candidates = (
            QRect(cx, target.top() - lh - 18, lw, lh),      # above
            QRect(cx, target.bottom() + 18, lw, lh),        # below
            QRect(target.right() + 18, cy, lw, lh),         # right (out to sea)
            QRect(target.left() - lw - 18, cy, lw, lh),     # left
        )
        for box in candidates:
            if clear(box):
                return box
        # Nowhere clean around it: stack upward until it stops colliding.
        box = QRect(cx, target.top() - lh - 18, lw, lh)
        for _ in range(len(placed) + 1):
            hit = next((pb for pb in placed
                        if box.intersects(pb.adjusted(-6, -6, 6, 6))), None)
            if hit is None:
                break
            box.moveTop(hit.top() - lh - 8)
        return box

    def _draw_callout(self, p, r: QRect, label, desc, placed):
        # amber box fit tightly around the whole section
        p.setPen(QPen(QColor(theme.ACCENT), 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r.adjusted(-3, -3, 3, 3))

        # Size the box to its wrapped description so no text is ever clipped.
        lw = 232
        p.setFont(theme.font(8))
        wrap = int(Qt.AlignmentFlag.AlignLeft) | int(Qt.TextFlag.TextWordWrap)
        needed = p.fontMetrics().boundingRect(QRect(0, 0, lw - 16, 400), wrap, desc)
        lh = 24 + needed.height() + 7
        box = self._place_box(lw, lh, r, placed)
        placed.append(QRect(box))

        p.setBrush(QColor(theme.PANEL_HI)); p.setPen(QPen(QColor(theme.ACCENT), 1)); p.drawRect(box)
        # Leader line from the box edge that faces the target back to the target —
        # vertical when the box sits above/below it, horizontal when off to a side.
        p.setPen(QPen(QColor(theme.ACCENT), 2))
        bc, tc = box.center(), r.center()
        if abs(tc.y() - bc.y()) >= abs(tc.x() - bc.x()):
            if bc.y() <= tc.y():
                p.drawLine(bc.x(), box.bottom(), tc.x(), r.top() - 3)
            else:
                p.drawLine(bc.x(), box.top(), tc.x(), r.bottom() + 3)
        elif bc.x() <= tc.x():
            p.drawLine(box.right(), bc.y(), r.left() - 3, tc.y())
        else:
            p.drawLine(box.left(), bc.y(), r.right() + 3, tc.y())
        p.setPen(QColor(theme.ACCENT)); p.setFont(theme.head(10, 1))
        p.drawText(box.adjusted(8, 5, -8, 0), Qt.AlignmentFlag.AlignLeft, label)
        p.setPen(QColor(theme.TEXT)); p.setFont(theme.font(8))
        p.drawText(box.adjusted(8, 24, -8, -5), wrap, desc)


class TipOverlay(QWidget):
    """A single centred 'field note' card drawn over a running battle to teach one
    new mechanic the first time it appears (e.g. oil platforms). It pauses the
    action like the full battle guide, but is one focused card with an icon,
    heading and a short brief — dismissed with the button to fight on.

    Built from the plotting-table material set (chamfered plate, hazard bar,
    engraved labels, an instrument lamp), so it reads as part of the war-room."""

    CARD_W = 540
    CARD_H = 292

    def __init__(self, parent: QWidget, canvas, title: str, body: str,
                 icon: str = "oilrig", on_close=None):
        super().__init__(parent)
        self.canvas = canvas
        self.title = title
        self.body = body
        self.icon = icon
        self._on_close = on_close
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setGeometry(parent.rect())
        parent.installEventFilter(self)

        # Hold the fight (and settle the guns) exactly as the full guide does.
        if canvas is not None:
            if hasattr(canvas, '_timer'):
                canvas._timer.stop()
            if hasattr(canvas, 'set_tutorial_freeze'):
                canvas.set_tutorial_freeze(True)

        self.btn = QPushButton("GOT IT  ✓", self)
        self.btn.setStyleSheet(theme.BTN_PRIMARY)
        self.btn.setFixedSize(230, 46)
        self.btn.clicked.connect(self._dismiss)
        self._place_button()
        self.raise_(); self.show()

    # ── lifecycle ────────────────────────────────────────────────────────────
    def eventFilter(self, obj, ev):
        if ev.type() == ev.Type.Resize:
            self.setGeometry(obj.rect()); self._place_button()
        return False

    def _card_rect(self) -> QRect:
        return QRect((self.width() - self.CARD_W) // 2,
                     (self.height() - self.CARD_H) // 2 - 10,
                     self.CARD_W, self.CARD_H)

    def _place_button(self):
        c = self._card_rect()
        self.btn.move(c.center().x() - self.btn.width() // 2, c.bottom() - 64)

    def _dismiss(self):
        if self.canvas is not None:
            if hasattr(self.canvas, 'set_tutorial_freeze'):
                self.canvas.set_tutorial_freeze(False)
            self.canvas._prev_ms = None                 # avoid a dt spike on resume
            if hasattr(self.canvas, '_timer'):
                self.canvas._timer.start(16)
            self.canvas.setFocus()
        self.deleteLater()
        if self._on_close is not None:
            self._on_close()

    # ── paint ────────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(6, 9, 12, 214))          # dim the battle behind

        c = self._card_rect()
        x, y, w, h = c.x(), c.y(), c.width(), c.height()
        theme.plate(p, x, y, w, h, base=theme.PANEL_HI, lit=True,
                    riveted=True, border=theme.LINE_HI)
        # Amber hazard header band — this is a live briefing, flagged as such.
        theme.hazard_bar(p, QRectF(x + 9, y, w - 18, 8), color=theme.ACCENT,
                         bg=theme.BG_DEEP)

        # Icon on its own lit sub-plate, top-left, with a phosphor 'live' lamp.
        ip = QRect(x + 22, y + 26, 74, 74)
        theme.plate(p, ip.x(), ip.y(), ip.width(), ip.height(),
                    base=theme.STEEL, lit=True, cut=7)
        theme.draw_icon(p, self.icon,
                        QRectF(ip.x() + 12, ip.y() + 12, ip.width() - 24, ip.height() - 24),
                        QColor(theme.ACCENT))
        theme.led(p, ip.right() - 6, ip.top() + 6, theme.PHOSPHOR, r=3.0)

        tx = ip.right() + 22
        theme.engraved_label(p, "FIELD NOTE", tx, y + 24, w - (tx - x) - 24,
                             theme.head(10, 3), color=theme.TEXT_DIM)
        theme.engraved_label(p, self.title, tx, y + 44, w - (tx - x) - 24,
                             theme.stencil(20, 2), color=theme.ACCENT)

        # Body brief, wrapped under the icon row.
        p.setPen(QColor(theme.TEXT)); p.setFont(theme.font(11))
        wrap = int(Qt.AlignmentFlag.AlignLeft) | int(Qt.TextFlag.TextWordWrap)
        p.drawText(QRect(x + 24, y + 116, w - 48, h - 190), wrap, self.body)


class RulesOverlay(QWidget):
    """A standalone 'how to play' briefing drawn over a menu screen (no battle
    behind it) — opened from the head-to-head setup's HOW TO PLAY button.

    It teaches the two things that decide a multiplayer match: capturing the
    central oil platform for score, and the flagship bosses that score buys. Each
    mechanic is a card on its own lit sub-plate with an icon (oil derrick /
    flagship), built entirely from the plotting-table material set so it reads as
    part of the war-room rather than a generic help pop-up."""

    _WRAP = int(Qt.AlignmentFlag.AlignLeft) | int(Qt.TextFlag.TextWordWrap)

    # (icon, tag, heading, body) for each mechanic card, laid left→right.
    CARDS = [
        ("oilrig", "OBJECTIVE", "CAPTURE THE PLATFORM",
         "One oil platform stands between the two bases. Steer your "
         "SURFACE ships into its ring to seize it — submarines run too deep and "
         "aircraft too high to plant a boarding crew. Capture and hold the objective for longer than your opponent and you will be rewarded."),
        ("boss", "ESCALATION", "FLAGSHIP BOSSES",
         "Once in a while the player with the most score gets to spawn a flagship, the first flagship sails in about three minutes; "
         "after that, every 90 seconds the side LEADING on score is awarded the "
         "next flagship from the fleet ladder: Potemkin first, then ever-stronger "
         "battleships"),
    ]

    PANEL_W = 760
    PAD     = 30        # panel inner horizontal padding
    GAP     = 24        # gap between the two cards
    ICON    = 58        # icon sub-plate size

    def __init__(self, parent: QWidget, on_close=None):
        super().__init__(parent)
        self._on_close = on_close
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setGeometry(parent.rect())
        parent.installEventFilter(self)

        self.btn = QPushButton("GOT IT  ✓", self)
        self.btn.setStyleSheet(theme.BTN_PRIMARY)
        self.btn.setFixedSize(230, 46)
        self.btn.clicked.connect(self._dismiss)
        self._place_button()
        self.raise_(); self.show()

    # ── lifecycle ────────────────────────────────────────────────────────────
    def eventFilter(self, obj, ev):
        if ev.type() == ev.Type.Resize:
            self.setGeometry(obj.rect()); self._place_button()
        return False

    def mousePressEvent(self, ev):
        # A click on the dimmed surround (outside the panel) also dismisses.
        if not self._geom()[0].contains(ev.pos()):
            self._dismiss()

    def _dismiss(self):
        self.deleteLater()
        if self._on_close is not None:
            self._on_close()

    # ── layout (deterministic; shared by paint + button placement) ─────────────
    def _card_height(self, body_w: int) -> int:
        """Height a card needs to show its heading row and fully-wrapped body — so
        no text is ever clipped, whatever the longest card body is."""
        fm = QFontMetrics(theme.font(11))
        body_h = max(
            fm.boundingRect(QRect(0, 0, body_w, 4000), self._WRAP, body).height()
            for _, _, _, body in self.CARDS)
        # 16 pad + icon row + 26 heading row + 8 gap + body + 16 pad
        return 16 + self.ICON + 26 + 8 + body_h + 16

    def _geom(self):
        """Returns (panel_rect, [card_rect, …], card_height). Everything is centred
        on the widget and sized to the wrapped text, so the guide fits any window."""
        W, H = self.width(), self.height()
        pw = max(520, min(self.PANEL_W, W - 48))
        card_w = (pw - 2 * self.PAD - self.GAP) // 2
        ch = self._card_height(card_w - 32)
        top = 108                                   # header band + intro line
        ph = top + ch + 22 + 46 + 26                # + button row + bottom pad
        px = (W - pw) // 2
        py = max(24, (H - ph) // 2)
        panel = QRect(px, py, pw, ph)
        cards = [QRect(px + self.PAD + i * (card_w + self.GAP), py + top, card_w, ch)
                 for i in range(len(self.CARDS))]
        return panel, cards, ch

    def _place_button(self):
        panel, _, _ = self._geom()
        self.btn.move(panel.center().x() - self.btn.width() // 2, panel.bottom() - 62)

    # ── paint ──────────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(6, 9, 12, 220))          # dim the menu behind

        panel, cards, _ = self._geom()
        x, y, w = panel.x(), panel.y(), panel.width()
        theme.plate(p, x, y, w, panel.height(), base=theme.PANEL_HI, lit=True,
                    riveted=True, border=theme.LINE_HI)
        theme.hazard_bar(p, QRectF(x + 9, y, w - 18, 8), color=theme.ACCENT,
                         bg=theme.BG_DEEP)

        # Header: dossier label + title + one-line brief on what wins the match.
        theme.engraved_label(p, "HEAD-TO-HEAD BRIEFING", x + self.PAD, y + 20,
                             w - 2 * self.PAD, theme.head(10, 3), color=theme.TEXT_DIM)
        theme.engraved_label(p, "HOW TO WIN", x + self.PAD, y + 38,
                             w - 2 * self.PAD, theme.stencil(22, 2), color=theme.ACCENT)
        p.setPen(QColor(theme.TEXT_DIM)); p.setFont(theme.font(11))
        p.drawText(QRect(x + self.PAD, y + 74, w - 2 * self.PAD, 24),
                   self._WRAP, "Destroy the enemy fort to win, and win the midfield to "
                   "get there faster.")

        for card, (icon, tag, heading, body) in zip(cards, self.CARDS):
            self._draw_card(p, card, icon, tag, heading, body)

    def _draw_card(self, p, r: QRect, icon: str, tag: str, heading: str, body: str):
        theme.plate(p, r.x(), r.y(), r.width(), r.height(), base=theme.STEEL,
                    lit=False, cut=8, border=theme.LINE)
        # Icon on its own lit sub-plate with a phosphor 'live' lamp, and the
        # mechanic's category engraved beside it.
        ip = QRect(r.x() + 16, r.y() + 16, self.ICON, self.ICON)
        theme.plate(p, ip.x(), ip.y(), ip.width(), ip.height(), base=theme.PANEL_HI,
                    lit=True, cut=6)
        theme.draw_icon(p, icon, QRectF(ip.x() + 11, ip.y() + 11,
                        ip.width() - 22, ip.height() - 22), QColor(theme.ACCENT))
        theme.led(p, ip.right() - 5, ip.top() + 5, theme.PHOSPHOR, r=3.0)
        theme.engraved_label(p, tag, ip.right() + 16, ip.y() + (self.ICON - 14) // 2,
                             r.right() - ip.right() - 26, theme.head(11, 4),
                             color=theme.TEXT_DIM)
        # Heading across the full card width, then the body wrapped below it —
        # full width so a long heading never crowds the icon or spills the card.
        theme.engraved_label(p, heading, r.x() + 16, ip.bottom() + 8,
                             r.width() - 32, theme.head(15, 2), color=theme.ACCENT)
        p.setPen(QColor(theme.TEXT)); p.setFont(theme.font(11))
        p.drawText(QRect(r.x() + 16, ip.bottom() + 34, r.width() - 32,
                         r.bottom() - (ip.bottom() + 34) - 12), self._WRAP, body)
