"""TrackOverlay: Renders asteroid/moving-object detection paths.

A candidate is a path across several raw frames, not a point on the
displayed (stacked) image, so each detection's sky coordinates are
projected into the displayed image's WCS before drawing.
"""


class TrackOverlay:
    """Draws asteroid-detection candidate tracks onto a 2D FITS image axis.

    Parameters
    ----------
    axis : `matplotlib.axes.Axes`
        The Matplotlib axis to render candidate tracks onto.
    config : `VisualizationConfig`
        Color configuration.
    """

    def __init__(self, axis, config):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.ax = axis
        self.config = config

    def render(self, candidates: list, wcs) -> list:  # ruff: ignore[missing-type-function-argument]
        """Draw each candidate's detections as a timestamp-ordered path.

        Parameters
        ----------
        candidates : `list` [`AsteroidDetectionCandidate`]
            Candidates to draw. Each one's `frame_detections` were
            taken from raw frames, not the displayed image, so they
            carry no pixel coordinates usable here directly -- only
            their RA/Dec, which this projects into `wcs`.
        wcs : `astropy.wcs.WCS`
            The displayed image's WCS, used to convert each
            detection's RA/Dec into that image's pixel coordinates.

        Returns
        -------
        track_lines : `list`
            The drawn `Line2D` track artists, one per candidate with
            at least one detection.
        """
        from astropy.coordinates import SkyCoord

        track_lines = []
        for candidate in candidates:
            detections = sorted(candidate.frame_detections, key=lambda d: d.timestamp)
            if not detections:
                continue

            sky = SkyCoord(
                ra=[d.right_ascension_deg for d in detections],
                dec=[d.declination_deg for d in detections],
                unit="deg",
            )
            pixel_x, pixel_y = wcs.world_to_pixel(sky)

            is_matched = candidate.ephemeris_match is not None
            color = self.config.matched_track_color if is_matched else self.config.unconfirmed_track_color

            (line,) = self.ax.plot(
                pixel_x,
                pixel_y,
                color=color,
                linestyle="-" if is_matched else "--",
                linewidth=2,
                marker="o",
                markersize=4,
                picker=True,
                clip_on=True,
            )
            track_lines.append(line)

            label = candidate.ephemeris_match.designation if is_matched else "unconfirmed mover"
            self.ax.text(
                pixel_x[-1],
                pixel_y[-1],
                label,
                color=color,
                fontsize=9,
                verticalalignment="center",
                clip_on=True,
            )

        return track_lines
