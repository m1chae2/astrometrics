"""Main interface for drawing pictures of frames and plots of results.

Astronomical data is stored in FITS files, which hold raw measurements that
an ordinary image viewer cannot show well. `Visualization` turns them into
pictures: `render_fits` draws one frame or stack, stretched so faint detail
shows, and `plot` draws the charts of a target's or a star's results. The
work happens in `pipelines/shared/image_conversions.py` and in the
`visualization/` package.
"""

from typing import TYPE_CHECKING, Literal

from matplotlib.figure import Figure

from astrometricslib.drivers.catalog_access import AbstractCatalogAccess
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.models.target import RenderedImage, Target, ViewableImage
from astrometricslib.pipelines.shared.api_arguments import (
    TargetLookup,
    check_choice,
    reject_unused_arguments,
    resolve_target,
)

if TYPE_CHECKING:
    from astrometricslib.api.stars import StellarCatalog

__all__ = ["Visualization"]

RENDER_KINDS = ("image", "data_url")
PLOT_KINDS = ("dashboard", "star", "astrometry", "photometry", "spectroscopy", "focus", "asteroids")

_PLOT_ARGUMENTS = {
    "dashboard": ("target", "selected_star", "limit", "figsize"),
    "star": ("star", "spectral_star", "figsize"),
    "astrometry": ("target", "limit", "figsize"),
    "photometry": ("target", "limit", "figsize"),
    "spectroscopy": ("target", "limit", "figsize"),
    "focus": ("target", "figsize"),
    "asteroids": ("target", "figsize"),
}

_DEFAULT_FIGURE_SIZES = {
    "dashboard": (16, 9),
    "star": (9, 8),
    "astrometry": (10, 10),
    "photometry": (16, 9),
    "spectroscopy": (16, 9),
    "focus": (14, 9),
    "asteroids": (10, 10),
}


class Visualization:
    """Draw FITS frames as pictures, and plot the results of the pipelines.

    A target or star id that names nothing in the library raises
    `NotFoundError`. A file that cannot be drawn raises `ProcessingError`.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings. They give the frames folder.
    storage : `AbstractCatalogAccess`
        The database the library reads and writes.
    targets : `TargetLookup`, optional
        The target catalog, used to turn a target name into a `Target`.
    stars : `StellarCatalog`, optional
        The star catalog, used to find a target's stars and to turn a star
        id into a `StellarObject`.
    """

    def __init__(
        self,
        config: AppConfiguration,
        storage: AbstractCatalogAccess,
        *,
        targets: TargetLookup | None = None,
        stars: StellarCatalog | None = None,
    ) -> None:
        self._config = config
        self._storage = storage
        self._targets = targets
        self._stars = stars

    def render_fits(
        self,
        path: str | None = None,
        target: str | Target | None = None,
        file_name: str | None = None,
        kind: Literal["image", "data_url"] = "image",
        iso: str | None = None,
        exposure: str | None = None,
        index: int = 0,
        max_dimensions: int | None = None,
        stretch: bool = True,
        center: float | None = None,
        width: float | None = None,
        crop_center_x: int | None = None,
        crop_center_y: int | None = None,
        crop_size: int | None = None,
    ) -> ViewableImage | RenderedImage:
        """Draw a FITS frame or stack as a picture. Nothing is saved.

        The frame is named in one of three ways: ``path``; ``target`` and
        ``file_name``; or ``target``, ``iso`` and ``exposure`` (and
        ``index``). Use it to judge a frame by eye: star trailing, clouds,
        gradients or a bad stack.

        Arguments used by each kind (any other argument is refused):

        - ``"image"``: all of them. ``crop_center_x``, ``crop_center_y`` and
          ``crop_size`` zoom in on one place.
        - ``"data_url"``: all except the three crop arguments.

        Parameters
        ----------
        path : `str`, optional
            Path to the FITS file.
        target : `str` or `Target`, optional
            The target whose frame to draw.
        file_name : `str`, optional
            Which of the target's frames: its file name, or the number at
            the end of the name, such as ``"013"``. It must match one frame.
        kind : `str`, optional
            ``"image"`` (default): a PNG with a description of the
            brightness range and crop, which an AI client receives as an
            image. ``"data_url"``: the picture as a data URL with its
            brightness range and FITS header, as the app's viewer shows it;
            a stretched stack uses the preview the stacking stage saved.
        iso : `str`, optional
            With ``target`` and ``exposure``: the gain or ISO of the frame.
        exposure : `str`, optional
            With ``target`` and ``iso``: the exposure length of the frame.
        index : `int`, optional
            With ``iso`` and ``exposure``: which matching frame, by order.
        max_dimensions : `int`, optional
            Longest side of the picture in pixels, from 100 to 2000.
            Defaults to 1200 for ``"image"`` and 2000 for ``"data_url"``.
        stretch : `bool`, optional
            Brighten faint detail automatically. Defaults to `True`. A
            stack's ``_processed.fits`` file is already stretched, so it is
            drawn as it is for ``"image"``.
        center : `float`, optional
            Middle of a manual brightness range, in pixel values. Needs
            ``width``.
        width : `float`, optional
            Width of a manual brightness range, in pixel values. Needs
            ``center``.
        crop_center_x : `int`, optional
            Column of the centre of a zoomed piece, in full-frame pixels.
        crop_center_y : `int`, optional
            Row of the centre of the zoomed piece, in full-frame pixels.
        crop_size : `int`, optional
            Side of the zoomed square in pixels. The piece is not shrunk
            below its own size.

        Returns
        -------
        picture : `ViewableImage` or `RenderedImage`
            A `ViewableImage` for ``"image"``, a `RenderedImage` for
            ``"data_url"``.

        Raises
        ------
        InvalidArgumentError
            If the kind is unknown, an argument the kind does not use is
            given, only one of ``center`` and ``width`` is given, only some
            crop values are given, or the frame is named in no way or in
            more than one way.
        """
        from astrometricslib.pipelines.shared import image_conversions

        check_choice("kind", kind, RENDER_KINDS)
        crop_values = (crop_center_x, crop_center_y, crop_size)
        reject_unused_arguments(
            kind,
            {"image": ("crop",), "data_url": ()},
            {"crop": any(value is not None for value in crop_values)},
        )
        if (center is None) != (width is None):
            raise InvalidArgumentError(
                "Give both center and width for a manual brightness range, or neither."
            )
        if any(value is not None for value in crop_values) and any(value is None for value in crop_values):
            raise InvalidArgumentError(
                "Give crop_center_x, crop_center_y and crop_size together, or none of them."
            )
        resolved, frame_path = self._frame_path(path, target, file_name, iso, exposure, index)
        default_size = 1200 if kind == "image" else 2000
        size = max(100, min(int(max_dimensions or default_size), 2000))
        if kind == "data_url":
            image_id = resolved.id if resolved is not None else ""
            return image_conversions.render_data_url(frame_path, image_id, size, stretch, center, width)
        crop = (crop_center_x, crop_center_y, crop_size) if crop_size is not None else None
        return image_conversions.render_viewable_image(frame_path, size, stretch, center, width, crop)

    def _frame_path(
        self,
        path: str | None,
        target: str | Target | None,
        file_name: str | None,
        iso: str | None,
        exposure: str | None,
        index: int,
    ) -> tuple[Target | None, str]:
        """Find the file a `render_fits` call names.

        Returns
        -------
        target : `Target` or `None`
            The target, when one was named.
        path : `str`
            The FITS file to draw.

        Raises
        ------
        InvalidArgumentError
            If the frame is named in no way or in more than one way.
        NotFoundError
            If the target or the frame does not exist.
        """
        import os

        from astrometricslib.pipelines.shared import image_conversions
        from astrometricslib.pipelines.shared.quality.frame_selection import file_in_range

        by_settings = iso is not None or exposure is not None
        if path is not None:
            if target is not None or file_name is not None or by_settings:
                raise InvalidArgumentError(
                    "Give a path, or a target with a file_name or iso and exposure, not both."
                )
            return None, path
        if target is None or (file_name is None) == (not by_settings):
            raise InvalidArgumentError("Give a path, or a target with a file_name or with iso and exposure.")
        resolved = resolve_target(self._targets, target)
        if by_settings:
            if iso is None or exposure is None:
                raise InvalidArgumentError("Give both iso and exposure.")
            try:
                found = image_conversions.get_frame(resolved, iso, exposure, index)
            except ValueError as error:
                raise NotFoundError(str(error), details={"target": resolved.id}) from error
            if not os.path.exists(found):
                raise NotFoundError(f"Frame not found: {found}", details={"path": found})
            return resolved, found
        matches = [
            frame.path
            for frame in resolved.frames
            if file_in_range(os.path.basename(frame.path), file_name, file_name)
        ]
        if len(matches) != 1:
            raise NotFoundError(
                f"{file_name!r} matches {len(matches)} frames of {resolved.id}; give the full file name.",
                details={"target": resolved.id, "file_name": file_name, "matches": len(matches)},
            )
        return resolved, matches[0]

    def get_last_captured_image(self, stretch: bool = True) -> RenderedImage | None:
        """Find the newest FITS file in the frames folder and draw it.

        Parameters
        ----------
        stretch : `bool`, optional
            Brighten faint detail automatically. Defaults to `True`.

        Returns
        -------
        image : `RenderedImage` or `None`
            The newest frame as a data URL, or `None` if there is no FITS
            file or it cannot be drawn.
        """
        from astrometricslib.pipelines.shared import image_conversions

        return image_conversions.get_last_captured_image(self._config, stretch)

    def plot(
        self,
        kind: Literal["dashboard", "star", "astrometry", "photometry", "spectroscopy", "focus", "asteroids"],
        target: str | Target | None = None,
        star: str | StellarObject | None = None,
        spectral_star: str | StellarObject | None = None,
        selected_star: str | StellarObject | None = None,
        limit: int = 15,
        figsize: tuple[int, int] | None = None,
    ) -> Figure:
        """Draw one of the charts of a target's or a star's results.

        Arguments used by each kind (any other argument is refused):

        - ``"dashboard"``: ``target``, ``selected_star``, ``limit``,
          ``figsize``.
        - ``"star"``: ``star``, ``spectral_star``, ``figsize``.
        - ``"astrometry"``, ``"photometry"``, ``"spectroscopy"``:
          ``target``, ``limit``, ``figsize``.
        - ``"focus"``, ``"asteroids"``: ``target``, ``figsize``.

        Parameters
        ----------
        kind : `str`
            ``"dashboard"``: the combined view of a processed target.
            ``"star"``: one star's light curve above its spectrum.
            ``"astrometry"``: the plate-solved star field.
            ``"photometry"``: the two-panel brightness view.
            ``"spectroscopy"``: the two-panel spectrum view.
            ``"focus"``: the focuser position and temperature through the
            night, and the star width against the temperature with its
            slope in pixels per degree, so a focus drift as the telescope
            cools is easy to see.
            ``"asteroids"``: the stack with the detected asteroid tracks.
        target : `str` or `Target`, optional
            The target to draw.
        star : `str` or `StellarObject`, optional
            For ``"star"``: the star (its id or the record).
        spectral_star : `str` or `StellarObject`, optional
            For ``"star"``: a separate record that holds the spectrum, if
            it is not on ``star``.
        selected_star : `str` or `StellarObject`, optional
            For ``"dashboard"``: a star to highlight in the light-curve panel.
        limit : `int`, optional
            Most stars to plot. Defaults to 15.
        figsize : `tuple` [`int`, `int`], optional
            Figure size in inches. Each kind has its own default.

        Returns
        -------
        figure : `matplotlib.figure.Figure`
            The chart.

        Raises
        ------
        InvalidArgumentError
            If the kind is unknown, an argument the kind does not use is
            given, or a needed target or star is missing. A ``"focus"``
            plot of a target with no focuser temperature is refused too.
        """
        from astrometricslib.visualization import helpers

        check_choice("kind", kind, PLOT_KINDS)
        reject_unused_arguments(
            kind,
            _PLOT_ARGUMENTS,
            {
                "target": target is not None,
                "star": star is not None,
                "spectral_star": spectral_star is not None,
                "selected_star": selected_star is not None,
                "limit": limit != 15,
                "figsize": figsize is not None,
            },
        )
        size = tuple(figsize) if figsize is not None else _DEFAULT_FIGURE_SIZES[kind]
        if kind == "star":
            if star is None:
                raise InvalidArgumentError("kind='star' needs a star.")
            return helpers.plot_stellar_analysis(
                self._resolve_star(star),
                spectral_star=self._resolve_star(spectral_star) if spectral_star is not None else None,
                figsize=size,
            )
        if target is None:
            raise InvalidArgumentError(f"kind={kind!r} needs a target.")
        resolved = resolve_target(self._targets, target)
        selected = self._resolve_star(selected_star) if selected_star is not None else None
        try:
            return self._plot_target(kind, resolved, selected, limit, size)
        except ValueError as error:
            # The drawing code refuses a target that lacks what the chart
            # needs, such as a stack or a focuser temperature.
            raise InvalidArgumentError(str(error), details={"target": resolved.id, "kind": kind}) from error

    def _plot_target(
        self,
        kind: str,
        target: Target,
        selected_star: StellarObject | None,
        limit: int,
        figsize: tuple[int, int],
    ) -> Figure:
        """Draw one of the charts of a target.

        Returns
        -------
        figure : `matplotlib.figure.Figure`
            The chart.
        """
        from astrometricslib.visualization import focus_plots, helpers

        if kind == "dashboard":
            return helpers.plot_target_dashboard(
                target, self._stars, limit=limit, figsize=figsize, selected_star=selected_star
            )
        if kind == "astrometry":
            return helpers.plot_astrometry(target, self._stars, limit=limit, figsize=figsize)
        if kind == "photometry":
            return helpers.plot_target_photometry(target, self._stars, limit=limit, figsize=figsize)
        if kind == "spectroscopy":
            return helpers.plot_target_spectroscopy(target, self._stars, limit=limit, figsize=figsize)
        if kind == "focus":
            return focus_plots.plot_focus_vs_temperature(target, figsize=figsize)
        return helpers.plot_asteroid_detection(target, figsize=figsize)

    def _resolve_star(self, star: str | StellarObject) -> StellarObject:
        """Turn a star id into the star's record.

        Returns
        -------
        star : `StellarObject`
            The star.

        Raises
        ------
        NotFoundError
            If no star has that id.
        """
        if isinstance(star, StellarObject):
            return star
        found = self._stars.get(star) if self._stars is not None else None
        if found is None:
            raise NotFoundError(f"No star with id {star!r} in the library.", details={"star": star})
        return found
