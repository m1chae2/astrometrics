"""Purpose: Plots that show how well the telescope stayed in focus.

Description: A telescope's focus drifts as the metal and glass cool during
the night, so a focuser left at one position gives softer pictures as the
temperature falls (on 2026-09-25 the focuser stayed at position 29987 while
its temperature fell from 22.3 C to 18.3 C, and the width of a star's
spectrum at 5000 A grew from 4.2 px to 6.9 px, about 0.7 px per degree).
The plots here put the focuser's position and temperature side by side, and
the star-image width when the frames have one, so a drift shows up before it
spoils a night's data.

Everything is read from the frame records of a target (`FrameRecord`), which
already hold the focuser position and temperature from each picture's FITS
header. Nothing here opens an image.
"""

from datetime import UTC, datetime
from typing import Any

import matplotlib.dates as matplotlib_dates
import matplotlib.pyplot as plt
import numpy as np

# The fewest frames with a star-image width that are worth fitting a line
# through, and the smallest temperature change that makes the slope
# meaningful. With fewer frames, or with the temperature almost unchanged,
# the fitted slope is mostly noise, so the plot shows the points without it.
MINIMUM_FRAMES_FOR_WIDTH_TREND = 5
MINIMUM_TEMPERATURE_SPAN_FOR_TREND_C = 0.5


def _collect_focus_samples(target: Any) -> dict[str, np.ndarray]:
    """Read the focuser readings out of a target's light frames.

    Frames that are not light frames, or that have no focuser temperature,
    are skipped. The rest are put in time order.

    Parameters
    ----------
    target : `Target`
        The target whose `frames` are read.

    Returns
    -------
    samples : `dict` [`str`, `numpy.ndarray`]
        ``"timestamp"``: seconds since 1970 (UTC), `NaN` where the frame has
        none. ``"temperature_c"``: focuser temperature in degrees Celsius.
        ``"position"``: focuser position in steps, `NaN` where missing.
        ``"star_width_px"``: the frame's star-image width in pixels (the mean
        of its x and y widths from alignment), `NaN` where missing.
    """
    timestamps = []
    temperatures = []
    positions = []
    widths = []
    for frame in getattr(target, "frames", None) or []:
        if str(getattr(frame, "role", "LIGHT")).upper() != "LIGHT":
            continue
        temperature = getattr(frame, "focuser_temperature_c", None)
        if temperature is None:
            continue
        measurements = getattr(frame, "measurements", None)
        width_x = getattr(measurements, "registration_fwhm_x_px", None)
        width_y = getattr(measurements, "registration_fwhm_y_px", None)
        timestamps.append(np.nan if getattr(frame, "timestamp", None) is None else float(frame.timestamp))
        temperatures.append(float(temperature))
        position = getattr(frame, "focuser_position", None)
        positions.append(np.nan if position is None else float(position))
        widths.append(
            np.nan if width_x is None or width_y is None else 0.5 * (float(width_x) + float(width_y))
        )
    samples = {
        "timestamp": np.array(timestamps, dtype=float),
        "temperature_c": np.array(temperatures, dtype=float),
        "position": np.array(positions, dtype=float),
        "star_width_px": np.array(widths, dtype=float),
    }
    if np.isfinite(samples["timestamp"]).all():
        order = np.argsort(samples["timestamp"], kind="stable")
        samples = {name: values[order] for name, values in samples.items()}
    return samples


def plot_focus_vs_temperature(target: Any, figsize: tuple[int, int] = (14, 9)) -> plt.Figure:
    """Plot a target's focuser position and temperature, and the focus quality.

    The figure has three panels:

    * Top: the focuser temperature (left scale) and position (right scale)
      through the night, one point per frame. A temperature that keeps
      falling while the position stays flat is a focus drift waiting to
      happen.
    * Bottom left: the focuser position against the temperature, coloured
      from the first frame to the last. A refocus shows up as a jump in
      position.
    * Bottom right: the star-image width against the temperature, when the
      frames have one, with a straight line through them and its slope in
      pixels per degree. A negative slope means the images get softer as it
      cools (the width falls as the temperature rises).

    Parameters
    ----------
    target : `Target`
        The target to plot. Its light frames must carry a focuser temperature.
    figsize : `tuple` [`int`, `int`], optional
        Figure size in inches. Defaults to ``(14, 9)``.

    Returns
    -------
    fig : `matplotlib.figure.Figure`
        The figure.

    Raises
    ------
    ValueError
        If no light frame of the target has a focuser temperature.
    """
    samples = _collect_focus_samples(target)
    temperature = samples["temperature_c"]
    if temperature.size == 0:
        raise ValueError(
            f"Target {getattr(target, 'id', 'unknown')!r} has no frames with a focuser temperature."
        )
    position = samples["position"]
    width = samples["star_width_px"]

    has_times = bool(np.isfinite(samples["timestamp"]).all())
    if has_times:
        when = np.array([
            matplotlib_dates.date2num(datetime.fromtimestamp(t, UTC)) for t in samples["timestamp"]
        ])
        time_label = "Time (UTC)"
    else:
        when = np.arange(temperature.size, dtype=float)
        time_label = "Frame number"

    plt.style.use("dark_background")
    fig = plt.figure(figsize=figsize)
    grid = fig.add_gridspec(2, 2, height_ratios=[1.0, 1.1], hspace=0.38, wspace=0.28)

    ax_time = fig.add_subplot(grid[0, :])
    ax_time.plot(when, temperature, "o", color="tab:orange", markersize=3)
    ax_time.set_ylabel("Focuser temperature (C)", color="tab:orange")
    ax_time.set_xlabel(time_label)
    ax_position = ax_time.twinx()
    ax_position.plot(when, position, "s", color="tab:cyan", markersize=3)
    ax_position.set_ylabel("Focuser position (steps)", color="tab:cyan")
    if has_times:
        ax_time.xaxis_date()
        ax_time.xaxis.set_major_formatter(matplotlib_dates.DateFormatter("%H:%M"))
    ax_time.set_title(f"{getattr(target, 'id', 'Target')} - focuser temperature and position")

    ax_scatter = fig.add_subplot(grid[1, 0])
    order = np.arange(temperature.size)
    points = ax_scatter.scatter(temperature, position, c=order, cmap="viridis", s=12)
    ax_scatter.set_xlabel("Focuser temperature (C)")
    ax_scatter.set_ylabel("Focuser position (steps)")
    ax_scatter.set_title("Position against temperature")
    fig.colorbar(points, ax=ax_scatter, label="Frame order (first to last)")

    ax_width = fig.add_subplot(grid[1, 1])
    has_width = np.isfinite(width)
    ax_width.set_xlabel("Focuser temperature (C)")
    ax_width.set_ylabel("Star-image width (px)")
    if has_width.any():
        ax_width.scatter(temperature[has_width], width[has_width], s=12, color="tab:green")
        enough_frames = has_width.sum() >= MINIMUM_FRAMES_FOR_WIDTH_TREND
        wide_enough = np.ptp(temperature[has_width]) >= MINIMUM_TEMPERATURE_SPAN_FOR_TREND_C
        if enough_frames and wide_enough:
            slope, intercept = np.polyfit(temperature[has_width], width[has_width], 1)
            line_x = np.array([temperature[has_width].min(), temperature[has_width].max()])
            ax_width.plot(line_x, slope * line_x + intercept, color="white", linewidth=1)
            ax_width.set_title(f"Star-image width against temperature ({slope:+.2f} px per C)")
        else:
            ax_width.set_title("Star-image width against temperature")
    else:
        ax_width.set_title("Star-image width against temperature")
        ax_width.text(
            0.5,
            0.5,
            "No frame has a star-image width yet\n(run alignment to measure it)",
            ha="center",
            va="center",
            transform=ax_width.transAxes,
        )
    return fig
