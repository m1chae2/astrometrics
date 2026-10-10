"""Purpose: The interface between the stacking pipeline and a stacking program.

Description: The stacking pipeline decides which frames to stack, how to
group them and how to judge the result. A stacking program (the "engine")
does the pixel work: it calibrates the frames, registers them (lines them
up) and combines them. `StackingDriver` is the abstract base class every
engine's driver implements. Siril is the engine in use
(`SirilStackingDriver` in `drivers/siril_stacking_driver.py`). Because the
pipeline only uses this interface, it never depends on one program's
commands or file formats.

A driver takes a `StackSettings` and returns a `StackRunResult`. Everything
the pipeline needs to know about the run, including values it used to read
from Siril's own files, comes back in the result's diagnostics.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, fields
from typing import Any


@dataclass(frozen=True)
class StackSettings:
    """The choices that shape one stack.

    Each value is `None` to use the engine's configured default.

    Attributes
    ----------
    rejection_sigma : `tuple` [`float`, `float`] or `None`
        The ``(low, high)`` limits, in standard deviations, past which a
        pixel value is thrown out. `None` derives them from the number of
        frames.
    filter_wfwhm : `str` or `None`
        A rule that drops blurry frames, such as ``"90%"`` to keep the
        sharpest 90%.
    filter_round : `str` or `None`
        A rule that drops frames whose stars are not round.
    stack_weight : `str` or `None`
        How frames are weighted when combined.
    generate_rejmap : `bool` or `None`
        Whether to save a picture of which pixels were thrown out.
    """

    rejection_sigma: tuple[float, float] | None = None
    filter_wfwhm: str | None = None
    filter_round: str | None = None
    stack_weight: str | None = None
    generate_rejmap: bool | None = None

    def as_options(self) -> dict[str, Any]:
        """List the settings that were given as keyword options.

        Returns
        -------
        options : `dict`
            Each setting that is not `None`, by name. The engine uses its
            own default for the rest.
        """
        return {
            item.name: getattr(self, item.name)
            for item in fields(self)
            if getattr(self, item.name) is not None
        }


@dataclass
class StackRunResult:
    """What one engine run produced.

    Attributes
    ----------
    stacked_path : `str` or `None`
        Where the stacked image was written, or `None` if the run failed.
    diagnostics : `dict`
        What the run reported: frame counts, the rejection limits used,
        calibration applied, registration results and similar. The stacking
        stage reads these to build the quality summary.
    engine_name : `str`
        The engine that made the stack.
    engine_version : `str` or `None`
        The engine's version, when it can be found.
    """

    stacked_path: str | None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    engine_name: str = ""
    engine_version: str | None = None


class StackingDriver(ABC):
    """What the stacking pipeline needs from a stacking program."""

    name: str
    """The program's name, recorded with each stack."""

    @abstractmethod
    def version(self) -> str | None:
        """Report the program's version.

        Returns
        -------
        version : `str` or `None`
            The version, or `None` when it cannot be found.
        """

    @abstractmethod
    def read_stack_artifacts(self, stacked_path: str) -> dict[str, Any]:
        """Read the engine's own files that sit next to a finished stack.

        The pipeline calls this once for the final stack, after any
        exposure groups were combined. It turns the engine's file formats
        into plain values, so no other code has to read them.

        Parameters
        ----------
        stacked_path : `str`
            The finished stack.

        Returns
        -------
        artifacts : `dict`
            ``"registration_frames"``, one dictionary per registered frame
            (``dx``, ``dy``, ``fwhm_x``, ``fwhm_y``, ``roundness``, ``rmse``
            and ``nb_stars``), in the order of the diagnostics'
            ``"symlinked_light_paths"``. ``"rejected_pixel_fraction"``, the
            share of pixel values thrown out, 0 to 1, or `None`. A value the
            engine did not produce is an empty list or `None`.
        """

    @abstractmethod
    def stack_batch(
        self,
        frames: list[Any],
        target_id: str,
        output_file: str,
        log_file: str | None,
        is_spectral: bool,
        settings: StackSettings,
        registration: str | None = None,
        job_id: str | None = None,
    ) -> StackRunResult:
        """Calibrate, register and stack frames that can be combined as one.

        Parameters
        ----------
        frames : `list`
            The frames, as dictionaries.
        target_id : `str`
            The target's name, used for the engine's working folder.
        output_file : `str`
            The file name of the stacked image.
        log_file : `str` or `None`
            Where the engine's output is logged.
        is_spectral : `bool`
            Whether these are spectroscopy frames.
        settings : `StackSettings`
            The stack's settings.
        registration : `str` or `None`
            For spectral frames, how to line the frames up. The names are
            the engine's own (see its documentation). `None` is the default.
        job_id : `str` or `None`
            The tracked job the run belongs to, so the engine can report
            progress against it.

        Returns
        -------
        result : `StackRunResult`
            The stacked path and what the run reported.
        """
