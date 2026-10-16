"""plotting.py — the plotting shared by the canvas objects that draw data: the
Spectrometer's screen and the Graph.

    curve = CurveImage()                      # one per object: it caches its image
    surf = curve.get(key, plot, vis, xs, ys, (xlo, xhi), (ylo, yhi), color=...)
    surface.blit(surf, vis.topleft)

`plot` is the plot area on screen, `vis` the part of it that's visible (zoomed
far in, most of a plot is off screen: only that part is drawn). xs / ys are in
plot space — already log10'd for a log axis. The image is redrawn only when
`key` (the data's identity), the size, the pan or the range changes.

Drawing rule — one column per screen pixel, so zoom decides the detail:
- sorted x with points closer than 1.5 px: each column draws its lowest-to-
  highest value (a narrow peak can never drop out between columns);
- points further apart: every point, joined in order (gaps at NaN);
- markers when asked, or (markers="auto") once points are 8 px apart.
Below MIN_PLOT_PX the caller shows a blank screen.

Also: tick positions for linear and log axes, tick labels, and packing float
columns into base64 text for the world to send to players (float32 — ~7
significant digits, fine on a log axis, unlike the spectrum's 16-bit ints).
"""

import base64
import math

import numpy as np
import pygame

MIN_PLOT_PX = 48            # a plot narrower than this shows a blank screen
DENSE_PX = 1.5              # points closer than this share columns: draw each column's range
MARKER_PX = 8               # markers="auto": dots once points are this far apart


# --- Ticks -----------------------------------------------------------------------------
def nice_step(span, max_ticks):
    """A round tick step (1, 2 or 5 × 10^k) giving at most max_ticks ticks over span."""
    raw = span / max(1, max_ticks)
    if not raw > 0 or not math.isfinite(raw):
        return 1.0
    k = 10 ** math.floor(math.log10(raw))
    return next(m * k for m in (1, 2, 5, 10) if m * k >= raw)


def linear_ticks(lo, hi, max_ticks):
    """[(position, value)] for a linear axis from lo to hi."""
    step = nice_step(hi - lo, max_ticks)
    first = math.ceil(lo / step - 1e-9)
    return [(k * step, k * step) for k in range(first, first + 1000) if k * step <= hi + 1e-9 * step]


def log_ticks(lo, hi, max_ticks):
    """[(position, value)] for a log axis; lo / hi are log10 of the ends. Whole
    decades (every few, if there are many); 2 and 5 between them when there's
    room; inside one decade, round values."""
    if hi - lo < 1.0:                          # under a decade: round values, placed on the log scale
        return [(math.log10(v), v) for _, v in linear_ticks(10 ** lo, 10 ** hi, max_ticks) if v > 0]
    decades = list(range(math.ceil(lo - 1e-9), math.floor(hi + 1e-9) + 1))
    every = max(1, math.ceil(len(decades) / max(1, max_ticks)))
    out = [(d, 10.0 ** d) for d in decades if d % every == 0]
    if len(decades) * 3 <= max_ticks:          # room for the 2s and 5s
        out += [(math.log10(m * 10.0 ** d), m * 10.0 ** d) for d in range(math.floor(lo), math.ceil(hi) + 1)
                for m in (2, 5) if lo <= math.log10(m * 10.0 ** d) <= hi]
    return sorted(out)


def format_tick(value):
    """A short label: 0, 0.25, 550, 1.2e-05, 3e+06."""
    if value == 0:
        return "0"
    a = abs(value)
    if a >= 1e5 or a < 1e-3:
        mantissa, exp = f"{value:.2e}".split("e")
        mantissa = mantissa.rstrip("0").rstrip(".")
        return f"{mantissa}e{int(exp)}"
    return f"{round(value, 10):g}"


def data_range(values, log=False, pad=0.05):
    """(lo, hi) to show `values` (plot space), padded a little; a sensible span
    for a single value; None if there's nothing finite."""
    v = values[np.isfinite(values)]
    if not len(v):
        return None
    lo, hi = float(np.min(v)), float(np.max(v))
    if hi - lo <= 0:
        half = 0.5 if log else (abs(lo) * 0.1 or 1.0)
        return lo - half, hi + half
    margin = (hi - lo) * pad
    return lo - margin, hi + margin


# --- Packing for the world --------------------------------------------------------------
def pack_floats(values):
    """Floats -> base64 text (float32, little-endian; NaN kept)."""
    return base64.b64encode(np.asarray(values, dtype="<f4").tobytes()).decode("ascii")


def unpack_floats(text):
    return np.frombuffer(base64.b64decode(text), dtype="<f4").astype(float)


# --- The curve --------------------------------------------------------------------------
class CurveImage:
    """The curve over the visible part of one plot, cached until something it
    depends on changes. One per drawing object."""

    def __init__(self):
        self._key, self._surf = None, None

    def get(self, key, plot, vis, xs, ys, x_range, y_range, *, color, style="lines",
            markers="auto", fill_colors=None, fill_alpha=70, sorted_x=False):
        """The image for vis (blit it at vis.topleft). style "lines" / "points" /
        "both"; fill_colors: a (vis.width, 3) uint8 array to fill under the curve
        with (a sorted-x curve only), or None. sorted_x: xs never decrease."""
        full = (key, plot.size, vis.x - plot.x, vis.y - plot.y, vis.size, tuple(x_range), tuple(y_range),
                tuple(color), style, markers, fill_alpha, fill_colors is not None and fill_colors.tobytes())
        if self._key != full:
            self._surf = self._draw(plot, vis, np.asarray(xs, float), np.asarray(ys, float), x_range, y_range,
                                    color, style, markers, fill_colors, fill_alpha, sorted_x)
            self._key = full
        return self._surf

    @staticmethod
    def _draw(plot, vis, xs, ys, x_range, y_range, color, style, markers, fill_colors, fill_alpha, sorted_x):
        (xlo, xhi), (ylo, yhi) = x_range, y_range
        w, h = plot.width, plot.height
        ox, oy = vis.x - plot.x, vis.y - plot.y               # the image's offset in the plot
        sx, sy = (w - 1) / (xhi - xlo), (h - 1) / (yhi - ylo)
        to_y = lambda v: (yhi - v) * sy - oy                  # noqa: E731
        ok = np.isfinite(xs) & np.isfinite(ys)
        fx, fy = xs[ok], ys[ok]
        px = (fx - xlo) * sx                                  # plot columns (floats)
        n = len(fx)
        spacing = (px[-1] - px[0]) / (n - 1) if sorted_x and n > 1 else float("inf")
        if not sorted_x and n > 1:                            # unsorted: the closest neighbours decide
            spacing = float(np.median(np.hypot(np.diff(px), np.diff((fy - ylo) * sy)))) or float("inf")
        lines = style in ("lines", "both")
        dense = sorted_x and lines and spacing < DENSE_PX

        img = np.zeros((vis.height, vis.width, 4), dtype=np.uint8)
        if sorted_x and n > 1 and (dense or fill_colors is not None):
            cols = np.arange(ox, ox + vis.width)
            at = np.interp(cols, px, fy, left=np.nan, right=np.nan)   # the curve at each column
            top, bot = at.copy(), at.copy()
            if dense:                                         # several points a column: each one's range
                c = np.floor(px).astype(int) - ox
                inside = (c >= 0) & (c < vis.width)
                filled_top, filled_bot = np.nan_to_num(top, nan=-np.inf), np.nan_to_num(bot, nan=np.inf)
                np.maximum.at(filled_top, c[inside], fy[inside])
                np.minimum.at(filled_bot, c[inside], fy[inside])
                top = np.where(np.isfinite(filled_top), filled_top, np.nan)
                bot = np.where(np.isfinite(filled_bot), filled_bot, np.nan)
            rows = np.arange(vis.height)[:, None]
            have = np.isfinite(top)[None, :]
            y_top = np.round(to_y(np.nan_to_num(top)))[None, :]
            y_bot = np.round(to_y(np.nan_to_num(bot)))[None, :]
            if fill_colors is not None:                       # under the curve, in each column's color
                fill = have & (rows >= y_top) & (rows <= round(to_y(max(ylo, 0.0))))
                img[..., :3] = np.where(fill[..., None], fill_colors[None, :, :], 0)
                img[..., 3] = np.where(fill, fill_alpha, 0)
            if dense:
                img[have & (rows >= y_top) & (rows <= y_bot)] = (*color, 255)
        surf = pygame.image.frombuffer(img.tobytes(), vis.size, "RGBA").copy()
        if n == 0:
            return surf

        # Points far enough apart to draw one by one: just the ones near the view.
        qx, qy = px - ox, to_y(fy)
        if sorted_x:
            first = max(0, int(np.searchsorted(px, ox)) - 1)
            last = min(n, int(np.searchsorted(px, ox + vis.width)) + 1)
        else:
            first, last = 0, n
        qx = np.clip(qx[first:last], -1e6, 1e6)
        qy = np.clip(qy[first:last], -1e6, 1e6)
        if lines and not dense and len(qx) >= 2:
            # A missing value (NaN) breaks the line: points between the same two gaps join up.
            group = np.cumsum(~ok)[ok][first:last]
            pts = list(zip(qx.tolist(), qy.tolist()))
            for g in np.unique(group):
                seg = [p for p, k in zip(pts, group) if k == g]
                if len(seg) >= 2:
                    pygame.draw.aalines(surf, color, False, seg)
                    if spacing >= 3:
                        pygame.draw.lines(surf, color, False, seg, 2)
        if style == "points":
            show_dots = True
        elif markers == "auto":
            show_dots = spacing >= (3 if style == "both" else MARKER_PX)
        else:
            show_dots = bool(markers)
        if show_dots:
            dot = max(2, min(4, round(h / 70))) if style == "points" else max(2, min(5, round(min(spacing, 40) / 8)))
            for x, y in zip(qx.tolist(), qy.tolist()):
                if -dot <= x <= vis.width + dot and -dot <= y <= vis.height + dot:
                    pygame.draw.circle(surf, color, (round(x), round(y)), dot)
        return surf
