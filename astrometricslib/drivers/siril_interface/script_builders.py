"""Siril script builders: the command lists the driver sends to Siril.

These are plain functions of their inputs. They read no files and start no
process, so the text they produce can be checked on its own.
"""

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# The two ways to find the stars that line spectroscopy frames up with one
# another, keyed by the name `process_target` takes in
# `spectral_star_detection`. Siril finds stars in every frame and matches
# them against a reference frame, so the number of stars found in the
# reference decides which frames can be matched at all.
#
# "standard" is what the pipeline used until 2026-09-19 (it is also the line
# every other stack uses). On the Vega spectroscopy session (140 frames,
# 0.5-5 s, 2026-05-24) it found 19 stars in the reference frame, matched 16
# of them in every frame, and registered all 140 frames (the 2026-08-25
# stack, documentation/evidence/stack_Vega.log).
#
# "relaxed" accepts less round and smaller stars, to find more of them in
# crowded fields. It was made the only setting on 2026-09-19. On the same
# Vega frames it found 76 "stars" in the reference frame (a mix of real stars
# and pieces of dispersed trails, with a typical size of 27 px against 3.6 px
# for the standard setting) and could not match 46 of the 140 frames; the
# frames it lost were mostly the short ones, which show fewer stars. It was
# added for fields with many stars (the comment it came with mentions 99-125
# stars per frame), and that case has not been re-tested here; that is why it
# is kept as a fallback (see `stack_frames`) instead of removed.
SPECTRAL_STAR_DETECTION_COMMANDS = {
    "standard": "setfindstar -relax=on",
    "relaxed": "setfindstar -relax=on -roundness=0.15 -radius=3",
}


# The commands that start every Siril script this module writes.
# ``setext fits`` makes Siril save FITS files, not another format.
# ``set32bits`` makes Siril save 32-bit floating-point pixels. Without it,
# Siril uses the bit depth in the user's own preferences. If that is 16-bit,
# Siril rounds the normalised stack to 65536 levels. Faint signal then loses
# precision, and the stack no longer scales linearly with the light collected
# after ``-norm=addscale``. Both commands last for the whole Siril session,
# so one line at the top of a script covers every ``convert``, ``calibrate``
# and ``stack`` after it. Siril 1.2 and later have ``set32bits``.
SIRIL_SCRIPT_PREAMBLE = ("setext fits", "set32bits")


# The key in `ImageProcessing.last_run_diagnostics` that holds the blocking
# calibration flags, one sentence each. The stacking pipeline reads it (see
# `calibration_gates` in `pipelines/stacking/pre_processing/`
# `assess_input_quality.py`) and fails its ``calibration_frame_count`` gate
# when the list is not empty.
CALIBRATION_BLOCKING_FLAGS_KEY = "calibration_blocking_flags"


# What a master built from too few frames costs, for each master kind. It ends
# the sentence that `calibration_count_flags` writes.
_SHORT_MASTER_CONSEQUENCES = {
    "bias": "the master bias keeps the read noise of its few frames and any cosmic ray hit in them",
    "dark": "the master dark keeps the noise of its few frames and any cosmic ray hit in them",
    "flat": "the master flat copies the noise of its few frames into every light frame",
}


def calibration_count_flags(
    num_biases: int, num_darks: int, num_flats: int, minimum_frames: int
) -> list[str]:
    """Find the masters that would be built from too few frames.

    A master is the combination of its source frames. The stacker averages the
    frames and rejects outlier pixel values (such as cosmic ray hits), and both
    steps need several frames. A master built from fewer frames than
    `minimum_frames` is still built, because the observatory's library can
    legitimately hold a single flat or dark. The shortfall is recorded
    instead, as a blocking flag: the stacking pipeline reads it, fails its
    ``calibration_frame_count`` gate and flags the stack, so the stack is not
    mistaken for a fully calibrated one. A master with no source frames at all
    is not built and is not flagged here.

    Parameters
    ----------
    num_biases : `int`
        How many bias frames are staged.
    num_darks : `int`
        How many dark frames are staged.
    num_flats : `int`
        How many flat frames are staged.
    minimum_frames : `int`
        The fewest frames a master should be built from
        (`AppConfiguration.get_minimum_calibration_frames`).

    Returns
    -------
    flags : `list` [`str`]
        One plain sentence for each master built from fewer than
        `minimum_frames` frames, in the order bias, dark, flat. Empty when
        every master that is built has enough frames.
    """
    flags = []
    for kind, count in (("bias", num_biases), ("dark", num_darks), ("flat", num_flats)):
        if 0 < count < minimum_frames:
            flags.append(
                f"{kind} master built from {count} frame(s), fewer than the minimum of "
                f"{minimum_frames}: {_SHORT_MASTER_CONSEQUENCES[kind]}"
            )
    return flags


def build_bias_master_commands(num_biases: int) -> list[str]:
    """Write the Siril commands that build the master bias.

    One bias frame is used as it is, because Siril's ``convert`` writes no
    sequence for a single frame and there is nothing to reject. Several are
    stacked with sigma rejection (``rej 3 3``), without normalisation, since
    a bias level is an absolute number. A master from fewer frames than the
    configured minimum is reported by `calibration_count_flags`, not changed
    here.

    Parameters
    ----------
    num_biases : `int`
        How many bias frames are staged. Must be at least 1.

    Returns
    -------
    commands : `list` [`str`]
        The commands, one per entry, starting after ``cd`` into the biases
        folder and ending with the master saved as ``bias_stacked``.
    """
    if num_biases == 1:
        return [
            "convert bias -out=../process",
            "cd ../process",
            "load bias_00001.fits",
            "save bias_stacked",
        ]
    return [
        "convert bias -out=../process -fitseq",
        "cd ../process",
        "stack bias rej 3 3 -nonorm -out=bias_stacked",
    ]


def build_dark_master_commands(num_darks: int) -> list[str]:
    """Write the Siril commands that build the master dark.

    One dark frame is used as it is, with no rejection, because Siril's
    ``convert`` writes no sequence for a single frame and there is nothing to
    compare it with. Several are stacked with sigma rejection (``rej 3 3``),
    without normalisation, since dark current is an absolute signal. A master
    from fewer frames than the configured minimum is reported by
    `calibration_count_flags`, not changed here.

    Parameters
    ----------
    num_darks : `int`
        How many dark frames are staged. Must be at least 1.

    Returns
    -------
    commands : `list` [`str`]
        The commands, one per entry, starting after ``cd`` into the darks
        folder and ending with the master saved as ``dark_stacked``.
    """
    if num_darks == 1:
        return [
            "convert dark -out=../process",
            "cd ../process",
            "load dark_00001.fits",
            "save dark_stacked",
        ]
    return [
        "convert dark -out=../process -fitseq",
        "cd ../process",
        "stack dark rej 3 3 -nonorm -out=dark_stacked",
    ]


def build_flat_master_commands(
    num_flats: int, has_bias: bool, color_filter_array_flags: str, smoothing_sigma: float | None
) -> list[str]:
    """Write the Siril commands that build the master flat.

    The bias master is subtracted from the flats whenever there is one, for a
    single flat as well as for several. A flat frame holds the bias level (the
    constant offset the camera adds to every pixel) on top of the light that
    reached the sensor. Dividing a light frame by a flat that still holds a
    bias pedestal divides by a number that is too large and too flat. The
    vignette (the dimming toward the corners) then looks shallower than it
    is, and the lights are under-corrected. A lone flat needs this as much as
    a set of flats does.

    Several flats are converted to one sequence, calibrated with the bias and
    stacked with rejection. Siril's ``convert`` writes no sequence for a
    single frame, so a lone flat is calibrated with ``calibrate_single``,
    which works on one file and writes it with the prefix ``pp_``. Without a
    bias master, a lone flat is used as it is. A flat set too noisy to use as
    it is gets a Gaussian blur (see `assess_staged_flats`). Siril's ``gauss``
    mirrors the image at its border. The master flat's own noise is white, so
    the blur removes it and keeps the vignette and dust shadows.

    Parameters
    ----------
    num_flats : `int`
        How many flat frames are staged.
    has_bias : `bool`
        Whether a master bias is available to subtract from the flats.
    color_filter_array_flags : `str`
        The colour-sensor flags for ``calibrate``, or an empty string.
    smoothing_sigma : `float` or `None`
        The Gaussian width in pixels, or `None` for no blur.

    Returns
    -------
    commands : `list` [`str`]
        The commands, one per entry, starting after ``cd`` into the flats
        folder and ending with the master saved as ``flat_stacked``.
    """
    smoothing = [f"gauss {smoothing_sigma:.4f}"] if smoothing_sigma else []
    if num_flats == 1:
        commands = ["convert flat -out=../process", "cd ../process"]
        if has_bias:
            commands += [
                f"calibrate_single flat_00001.fits -bias=bias_stacked{color_filter_array_flags}",
                "load pp_flat_00001.fits",
            ]
        else:
            commands.append("load flat_00001.fits")
        return [*commands, *smoothing, "save flat_stacked"]
    commands = [
        "convert flat -out=../process -fitseq",
        "cd ../process",
        f"calibrate flat {'-bias=bias_stacked' if has_bias else ''}{color_filter_array_flags}",
        "stack pp_flat rej 3 3 -norm=mul -out=flat_stacked",
    ]
    if smoothing:
        commands += ["load flat_stacked", *smoothing, "save flat_stacked"]
    return commands


def build_single_light_commands(
    dark_flag: str, flat_flag: str, bias_flag: str, color_filter_array_flags: str, debayer_flag: str
) -> list[str]:
    """Write the Siril commands for a stack of exactly one light frame.

    A single light has nothing to register or reject against, but it still
    needs the same calibration as every other light: the dark removes the
    sensor's thermal signal and bias, and the flat removes vignetting and
    dust shadows. Siril's ``convert`` writes no sequence for one frame, so the
    sequence command ``calibrate`` cannot be used. ``calibrate_single`` takes
    the same options for one file and writes it with the prefix ``pp_``. When
    no calibration master exists, there is nothing to apply and the frame is
    saved as it is.

    Parameters
    ----------
    dark_flag, flat_flag, bias_flag : `str`
        The ``calibrate`` options for each master, or an empty string when it
        is not applied (see `light_calibration_flags`).
    color_filter_array_flags : `str`
        The colour-sensor flags for ``calibrate``, or an empty string.
    debayer_flag : `str`
        ``" -debayer"`` for a colour sensor, otherwise an empty string.

    Returns
    -------
    commands : `list` [`str`]
        The commands, one per entry, starting after ``cd`` into the lights
        folder and ending with the frame saved as ``result_stacked``.
    """
    commands = ["convert light_source -out=../process", "cd ../process"]
    if dark_flag or flat_flag or bias_flag:
        options = " ".join(flag for flag in (dark_flag, flat_flag, bias_flag) if flag)
        commands += [
            f"calibrate_single light_source_00001.fits {options}{color_filter_array_flags}{debayer_flag}",
            "load pp_light_source_00001.fits",
        ]
    else:
        commands.append("load light_source_00001.fits")
    return [*commands, "save result_stacked"]


def light_calibration_flags(num_darks: int, num_flats: int, num_biases: int) -> tuple[str, str, str]:
    """Choose which calibration masters Siril applies to the light frames.

    A dark master is made from raw dark frames, so it already holds the
    camera's bias level. Siril subtracts a bias master as well when both are
    given, which removes the bias twice: on the Nikon D5300 (bias 598, a 30 s
    light of about 672) that left every pixel below zero and the stack was
    blank (see
    `documentation/evidence/stack_exposure_groups_20260920.json`). So a bias
    master is applied to the lights only when there is no dark master.
    Flats are
    calibrated with the bias master in their own command and are not affected.

    Parameters
    ----------
    num_darks : `int`
        How many dark frames were found for the lights' exposure.
    num_flats : `int`
        How many flat frames were found.
    num_biases : `int`
        How many bias frames were found.

    Returns
    -------
    dark_flag, flat_flag, bias_flag : `str`
        The ``calibrate`` options for each master, or an empty string when it
        is not applied.
    """
    dark_flag = "-dark=dark_stacked" if num_darks > 0 else ""
    flat_flag = "-flat=flat_stacked" if num_flats > 0 else ""
    bias_flag = "-bias=bias_stacked" if num_biases > 0 and num_darks == 0 else ""
    return dark_flag, flat_flag, bias_flag


def color_filter_array_flags(uses_color_filter_array: bool) -> str:
    """Give the ``calibrate`` options that handle a color (Bayer) sensor.

    Parameters
    ----------
    uses_color_filter_array : `bool`
        Whether the staged frames come from a color sensor.

    Returns
    -------
    flags : `str`
        The options, with a leading space, or an empty string for a
        monochrome sensor.
    """
    return " -cfa -equalize_cfa" if uses_color_filter_array else ""


@dataclass(frozen=True)
class StagedFrameCounts:
    """How many frames were staged for each part of one run.

    Parameters
    ----------
    biases, darks, flats, lights : `int`
        The number of staged bias, dark, flat and light frames. The real
        counts matter even when a master is restored from the cache: the
        lights' ``calibrate`` command reads them to decide which masters
        to apply.
    """

    biases: int
    darks: int
    flats: int
    lights: int


def build_master_calibration_commands(
    target_folder: str,
    counts: StagedFrameCounts,
    restored_master_kinds: set[str],
    uses_color_filter_array: bool,
    flat_smoothing_sigma: float | None,
) -> list[str]:
    """Build the commands that make the bias, dark and flat masters.

    A master already restored from the cache is skipped. Only the build is
    skipped: the counts keep their real values, because the lights'
    ``calibrate`` command reads them to decide whether to pass
    ``-bias=``, ``-dark=`` and ``-flat=``.

    Parameters
    ----------
    target_folder : `str`
        The run's work folder.
    counts : `StagedFrameCounts`
        The staged frame counts.
    restored_master_kinds : `set` [`str`]
        The masters (``"bias"``, ``"dark"``, ``"flat"``) restored from the
        cache.
    uses_color_filter_array : `bool`
        Whether the frames come from a color sensor.
    flat_smoothing_sigma : `float` or `None`
        The blur width for a master flat built now, or `None` for none.

    Returns
    -------
    commands : `list` [`str`]
        The commands, in the order Siril runs them.
    """
    script: list[str] = []
    if counts.biases > 0 and "bias" not in restored_master_kinds:
        script += [f"cd {os.path.join(target_folder, 'biases')}"]
        script += build_bias_master_commands(counts.biases)

    if counts.darks > 0 and "dark" not in restored_master_kinds:
        script += [f"cd {os.path.join(target_folder, 'darks')}"]
        script += build_dark_master_commands(counts.darks)

    if counts.flats > 0 and "flat" not in restored_master_kinds:
        script += [f"cd {os.path.join(target_folder, 'flats')}"]
        script += build_flat_master_commands(
            counts.flats,
            counts.biases > 0,
            color_filter_array_flags(uses_color_filter_array),
            flat_smoothing_sigma,
        )
    return script


@dataclass(frozen=True)
class LightScriptSettings:
    """The inputs that decide the commands for the light frames.

    Parameters
    ----------
    counts : `StagedFrameCounts`
        The staged frame counts.
    uses_color_filter_array : `bool`
        Whether the frames come from a color sensor.
    is_spectral : `bool`
        Whether the frames are spectroscopy frames.
    spectral_star_detection : `str`
        A key of `SPECTRAL_STAR_DETECTION_COMMANDS`, or
        ``"phase_correlation"``.
    rejection_sigma_low, rejection_sigma_high : `float`
        The sigma-clipping bounds of the stack.
    filter_wfwhm, filter_round : `str` or `None`
        Siril's frame filters, or `None` for none.
    stack_weight : `str` or `None`
        Siril's ``-weight`` value, or `None` for none.
    generate_rejmap : `bool`
        Whether to make a rejection map.
    reference_frame_path : `str` or `None`
        The frame to line the others up with, or `None` to let Siril choose.
    light_paths : `list` [`str`]
        The real paths of the staged lights, in the order they were staged.
        Siril counts the sequence from 1 in this order.
    """

    counts: StagedFrameCounts
    uses_color_filter_array: bool
    is_spectral: bool
    spectral_star_detection: str
    rejection_sigma_low: float
    rejection_sigma_high: float
    filter_wfwhm: str | None
    filter_round: str | None
    stack_weight: str | None
    generate_rejmap: bool
    reference_frame_path: str | None
    light_paths: list[str]


@dataclass(frozen=True)
class LightScript:
    """The commands for the light frames and what they imply.

    Parameters
    ----------
    commands : `list` [`str`]
        The commands, starting with the change to the lights folder.
    sequence_name : `str` or `None`
        The calibrated sequence's name, or `None` for a single light, which
        has no sequence.
    uses_phase_correlation : `bool`
        Whether the register and stack commands are left out because the
        frames are lined up in Python.
    calibration_applied : `dict` [`str`, `bool`]
        Which masters the lights are calibrated with.
    """

    commands: list[str]
    sequence_name: str | None
    uses_phase_correlation: bool
    calibration_applied: dict[str, bool]


def build_light_script(target_folder: str, settings: LightScriptSettings) -> LightScript:
    """Build the commands that calibrate, register and stack the lights.

    Parameters
    ----------
    target_folder : `str`
        The run's work folder.
    settings : `LightScriptSettings`
        What decides the commands.

    Returns
    -------
    light_script : `LightScript`
        The commands and what they imply for the rest of the run.
    """
    counts = settings.counts
    num_lights = counts.lights
    num_darks, num_flats, num_biases = counts.darks, counts.flats, counts.biases
    uses_color_filter_array = settings.uses_color_filter_array
    cfa_flags = color_filter_array_flags(uses_color_filter_array)
    is_spectral = settings.is_spectral
    spectral_star_detection = settings.spectral_star_detection
    filter_wfwhm = settings.filter_wfwhm
    filter_round = settings.filter_round

    # Process Lights
    script = [f"cd {os.path.join(target_folder, 'lights')}"]
    # only set in the multi-frame branch below; single-frame
    # stacks skip registration entirely
    seq = None
    # Read below and by the caller, past the num_lights branch, so it needs
    # to exist either way -- a single-frame "stack" never has more than one
    # frame to align.
    use_phase_correlation_alignment = (
        num_lights > 1 and is_spectral and spectral_star_detection == "phase_correlation"
    )
    calibration_applied: dict[str, bool]
    if num_lights == 1:
        # Siril's 'convert' does not create a .seq file for
        # a single input frame, so sequence-based
        # calibrate/register/stack commands can't be used
        # here. The frame is calibrated as a single file and
        # saved as the result: skipping the dark and flat would
        # record an uncalibrated frame as the target's stack.
        dark_flag, flat_flag, bias_flag = light_calibration_flags(num_darks, num_flats, num_biases)
        calibration_applied = {
            "dark": bool(dark_flag),
            "flat": bool(flat_flag),
            "bias": bool(bias_flag),
        }
        script += build_single_light_commands(
            dark_flag,
            flat_flag,
            bias_flag,
            cfa_flags,
            " -debayer" if uses_color_filter_array else "",
        )
    else:
        # -fitseq stores the whole sequence as one file, which is
        # fine when Siril also does the registration -- but the
        # phase-correlation path (see
        # `_stack_phase_correlation_aligned_spectral_frames`)
        # reads each calibrated frame back out in Python, which a
        # loose per-frame FITS file (Siril's other convert format)
        # makes trivial and a packed fitseq file does not.
        convert_flags = "" if use_phase_correlation_alignment else " -fitseq"
        script += [f"convert light_source -out=../process{convert_flags}", "cd ../process"]

        has_cal = num_darks > 0 or num_biases > 0 or num_flats > 0
        if has_cal:
            dark_flag, flat_flag, bias_flag = light_calibration_flags(num_darks, num_flats, num_biases)
            calibration_applied = {
                "dark": bool(dark_flag),
                "flat": bool(flat_flag),
                "bias": bool(bias_flag),
            }
            # -debayer belongs only to the lights: it is what turns
            # a CFA mosaic into an RGB image, and is meaningless
            # (and harmful) on monochrome data.
            debayer_flag = " -debayer" if uses_color_filter_array else ""
            script.append(
                f"calibrate light_source {dark_flag} {flat_flag} {bias_flag}{cfa_flags}{debayer_flag}"
            )
            seq = "pp_light_source"
        else:
            seq = "light_source"
            calibration_applied = {
                "dark": False,
                "flat": False,
                "bias": False,
            }

        # Multi-frame Stacking
        # Spectroscopy frames need a shift-only transform: a
        # diffraction grating disperses every star's light,
        # not just the target's, so field stars are usually
        # still plentiful (confirmed empirically -- 99-125
        # stars/frame in a real SA200 session is typical,
        # not "too few" for a homography fit). The actual
        # constraint is that any rotation or scale
        # introduced by registration would rotate the
        # dispersion axis and smear the spectral trace
        # between frames, breaking wavelength calibration
        # consistency across the stack.
        # Pin star detection instead of inheriting whatever the
        # Siril GUI last recorded to its own config: detection
        # settings are global user state, so without this the
        # pipeline's registration behaviour differs between
        # machines and silently changes if someone adjusts the
        # GUI. -relax=on is what M 42 needs -- its nebulosity
        # dominates the frame's background statistics, and with
        # the default relax=off Siril's findstar returned 9
        # candidates on a calibrated frame carrying 464
        # detectable stars (photutils DAOStarFinder, 5 sigma),
        # which aborted registration for the whole sequence.
        # With relax=on the same frame yields 472.
        register_commands = ["setfindstar -relax=on"]

        # Field-star population is typically rich enough in
        # spectral frames too (see below), so FWHM/roundness
        # selection applies to both frame types -- they're a
        # proxy for overall seeing/focus quality, not something
        # specific to standard imaging. Which command carries
        # them differs by path; see each branch.
        frame_filter_options = []
        if filter_wfwhm:
            frame_filter_options.append(f"-filter-wfwhm={filter_wfwhm}")
        if filter_round:
            frame_filter_options.append(f"-filter-round={filter_round}")

        if use_phase_correlation_alignment:
            # No register/stack commands go into this script at
            # all: `_stack_phase_correlation_aligned_spectral_frames`
            # sends what has been built so far (calibration only)
            # as its own complete run, then drives a second Siril
            # run itself once the frames are aligned in Python.
            pass
        elif is_spectral:
            # Multi-frame Stacking
            # Spectroscopy frames need a shift-only transform: a
            # diffraction grating disperses every star's light,
            # not just the target's, so field stars are usually
            # still plentiful (confirmed empirically -- 99-125
            # stars/frame in a real SA200 session is typical,
            # not "too few" for a homography fit). The actual
            # constraint is that any rotation or scale
            # introduced by registration would rotate the
            # dispersion axis and smear the spectral trace
            # between frames, breaking wavelength calibration
            # consistency across the stack.
            #
            # Left on single-pass registration deliberately: the
            # two-pass reference selection below is validated
            # only against standard frames, and the spectral
            # path's shift-only constraint is the more delicate
            # of the two to disturb.
            #
            # Single-pass `register` applies its transforms as
            # it goes and offers no separate filtering step, so
            # this path's frame filters stay on `stack` below.
            # Which stars register the frames is chosen by
            # `spectral_star_detection`; see
            # SPECTRAL_STAR_DETECTION_COMMANDS for the two
            # settings and why the standard one is the default.
            register_commands = [SPECTRAL_STAR_DETECTION_COMMANDS[spectral_star_detection]]
            register_commands.append(f"register {seq} -transf=shift")
            registered_seq = f"r_{seq}"
            stack_filter_options = frame_filter_options
        else:
            # Two-pass registration scores every frame before
            # picking a reference, where single-pass just takes
            # the first. M 42's first frame is also its worst
            # for this purpose -- pointing sits ~9.6 arcmin off
            # the other 21 -- and single-pass registration
            # matched only 6-8 star pairs against it and
            # registered nothing, even with detection fixed.
            # Two-pass chose a different reference and stacked
            # 21 of 22 frames.
            if settings.reference_frame_path is not None:
                # The caller names the reference (the odd–even
                # difference job needs both halves on the library
                # stack's reference). Siril counts sequence images
                # from 1, in the order of the symlinked light frames.
                light_paths = settings.light_paths
                if settings.reference_frame_path in light_paths:
                    register_commands.append(
                        f"setref {seq} {light_paths.index(settings.reference_frame_path) + 1}"
                    )
            register_commands.append(f"register {seq} -2pass")
            # -2pass computes the transforms without applying
            # them, so the registered sequence only exists once
            # seqapplyreg has run.
            #
            # The frame filters ride on seqapplyreg rather than
            # on `stack` below. Both commands accept the same
            # -filter-* options and select on the same per-frame
            # FWHM/roundness that -2pass just measured, so the
            # surviving frame set is the same either way -- but
            # filtering here means a rejected frame is never
            # interpolated and never written to disk at all,
            # where filtering at stack time pays for the
            # full-resolution resample first and discards the
            # result afterwards. On the 238-frame M 106 run
            # whose intermediates measured 65GB, a 90% wfwhm
            # setting is ~24 frames of resample-and-write
            # avoided.
            register_commands.append(" ".join([f"seqapplyreg {seq}", *frame_filter_options]))
            registered_seq = f"r_{seq}"
            # Deliberately empty: seqapplyreg already dropped
            # those frames, and passing the same filters again
            # would filter an already-filtered sequence,
            # compounding to 81% of the input for a 90% setting.
            stack_filter_options = []

        if not use_phase_correlation_alignment:
            stack_options = [f"rej {settings.rejection_sigma_low:.4f} {settings.rejection_sigma_high:.4f}"]
            stack_options += stack_filter_options
            if settings.stack_weight:
                stack_options.append(f"-weight={settings.stack_weight}")
            if settings.generate_rejmap:
                stack_options.append("-rejmap")
            stack_options += ["-norm=addscale", "-out=result_stacked"]

            script += [
                *register_commands,
                f"stack {registered_seq} " + " ".join(stack_options),
            ]

    return LightScript(
        commands=script,
        sequence_name=seq,
        uses_phase_correlation=use_phase_correlation_alignment,
        calibration_applied=calibration_applied,
    )
