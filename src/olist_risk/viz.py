"""Shared chart style. Palette validated with the dataviz validator (2 categorical slots,
all checks pass in light mode): blue = primary series, orange = comparison / alert."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: figures are written to disk, never shown

import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

BLUE = "#2a78d6"
ORANGE = "#eb6834"
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
SURFACE = "#fcfcfb"
BAND = "#eeeeea"  # neutral shading for excluded / highlighted periods

EXCLUDED_START = pd.Timestamp("2018-05-16")  # centres the band on the Jun-Aug month points
EXCLUDED_END = pd.Timestamp("2018-08-16")


def apply_style() -> None:
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "font.family": "sans-serif",
        "font.sans-serif": ["Segoe UI", "DejaVu Sans", "Arial"],
        "font.size": 10, "text.color": INK,
        "axes.edgecolor": AXIS, "axes.labelcolor": INK_2,
        "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
        "axes.grid": True, "axes.axisbelow": True,
        "grid.color": GRID, "grid.linewidth": 0.8,
        "xtick.color": MUTED, "ytick.color": MUTED,
        "xtick.bottom": False, "ytick.left": False,
        "lines.linewidth": 2.0, "legend.frameon": False,
    })


def new_axes(width: float = 10.0, height: float = 5.6) -> tuple[Figure, Axes]:
    apply_style()
    fig, ax = plt.subplots(figsize=(width, height))
    return fig, ax


def esc(text: str) -> str:
    """Escape $ so matplotlib never treats two currency signs as a math expression."""
    return text.replace("$", r"\$")


def title_block(fig: Figure, headline: str, subtitle: str | None = None, left: float = 0.09) -> None:
    """Headline states the finding; subtitle states the measure and window.
    `left` is the plot's left margin; widen it when y tick labels are long (horizontal bars)."""
    fig.subplots_adjust(top=0.80 if subtitle else 0.88, left=left, right=0.97, bottom=0.12)
    fig.text(0.09, 0.945, esc(headline), fontsize=14, fontweight="bold", ha="left", va="top")
    if subtitle:
        fig.text(0.09, 0.885, esc(subtitle), fontsize=10, color=INK_2, ha="left", va="top")


def source_note(fig: Figure, text: str = "Source: Olist public dataset, 2017-01 to 2018-08. Profit = proxy.") -> None:
    fig.text(0.09, 0.02, esc(text), fontsize=8, color=MUTED, ha="left")


def money_k(ax: Axes, axis: str = "y") -> None:
    fmt = FuncFormatter(lambda v, _: f"{'-' if v < 0 else ''}R\\${abs(v) / 1000:,.0f}k")
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(fmt)


def pct(ax: Axes, axis: str = "y", decimals: int = 0) -> None:
    fmt = FuncFormatter(lambda v, _: f"{v * 100:.{decimals}f}%")
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(fmt)


def shade_excluded(ax: Axes, label: str = "Excluded from\nlate-delivery model") -> None:
    ax.axvspan(EXCLUDED_START, EXCLUDED_END, color=BAND, zorder=0, lw=0)
    ymax = ax.get_ylim()[1]
    ax.text(pd.Timestamp("2018-07-01"), ymax * 0.98, label, ha="center", va="top",
            fontsize=8, color=INK_2)


def end_label(ax: Axes, x, y, text: str, color: str, dy: float = 0.0) -> None:
    """Direct label at the last point of a line (legend-free, colour follows the series)."""
    ax.annotate(text, (x, y), xytext=(6, dy), textcoords="offset points",
                color=color, fontsize=9, fontweight="bold", va="center")


def bar_label(ax: Axes, x: float, y: float, text: str, **kw) -> None:
    """Value label with a surface-coloured halo so gridlines never run through the text."""
    ax.text(x, y, esc(text), va=kw.pop("va", "center"), fontsize=kw.pop("fontsize", 8.5),
            color=kw.pop("color", INK_2), bbox={"fc": SURFACE, "ec": "none", "pad": 1.5}, **kw)


def save(fig: Figure, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path
