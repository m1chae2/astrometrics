"""Purpose: Short register and stack scripts for the library's study scripts.

Description: Two scripts in ``astrometricslib/scripts`` compare the stacking
pipeline's own steps with Siril's, on a few frames in a scratch folder:

* ``compare_group_steps_with_siril.py`` lines up a short list of images with
  Siril (`build_shift_registration_commands`) and stacks already aligned
  images with Siril's noise weights (`build_noise_weighted_blend_commands`).
* ``spectral_registration_quality_analysis.py`` registers a target's
  spectroscopy frames the way the spectral stacking path does
  (`build_spectral_registration_check_commands`) and reads the star lists.

The folder they run in holds ``lights/light_source_<n>.fits`` and an empty
``process`` folder. They are run with `core.script_run.run_siril_script`.
"""

# The oldest Siril that has the commands used here.
MINIMUM_SIRIL_VERSION = "1.2.0"

# The commands that convert the frames in ``lights`` into a sequence in
# ``process``, after the 32-bit FITS preamble.
_CONVERT_COMMANDS = (
    "setext fits",
    "set32bits",
    "cd lights",
    "convert light_source -out=../process",
    "cd ../process",
)


def build_shift_registration_commands(detection_command: str) -> list[str]:
    """Write the script that lines up the images with a shift only.

    Parameters
    ----------
    detection_command : `str`
        The ``setfindstar`` command for the star detection, such as one of
        ``SPECTRAL_STAR_DETECTION_COMMANDS``.

    Returns
    -------
    commands : `list` [`str`]
        The script. Siril writes the shifted images as
        ``process/r_light_source_<n>.fits``.
    """
    return [
        f"requires {MINIMUM_SIRIL_VERSION}",
        *_CONVERT_COMMANDS,
        detection_command,
        "register light_source_ -transf=shift",
        "close",
    ]


def build_noise_weighted_blend_commands() -> list[str]:
    """Write the script that stacks aligned images weighted by their noise.

    Returns
    -------
    commands : `list` [`str`]
        The script. Siril writes the stack as ``process/blend.fits``.
    """
    return [
        f"requires {MINIMUM_SIRIL_VERSION}",
        *_CONVERT_COMMANDS,
        "stack light_source_ rej n 3 3 -norm=addscale -weight=noise -out=blend",
        "close",
    ]


def build_spectral_registration_check_commands() -> list[str]:
    """Write the script that registers spectroscopy frames with a shift only.

    The frames are kept in one FITS sequence file and registered as the
    spectral stacking path registers them. Nothing is stacked.

    Returns
    -------
    commands : `list` [`str`]
        The script. Siril writes ``process/light_source.seq`` and one star
        list per frame in ``process/cache``.
    """
    return [
        f"requires {MINIMUM_SIRIL_VERSION}",
        "cd lights",
        "convert light_source -out=../process -fitseq",
        "cd ../process",
        "register light_source -transf=shift",
    ]
