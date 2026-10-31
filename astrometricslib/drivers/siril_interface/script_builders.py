"""Siril script builders: the command lists the driver sends to Siril.

These are plain functions of their inputs. They read no files and start no
process, so the text they produce can be checked on its own.
"""

import logging

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
