"""Purpose: Fit the INDI simulators' settings to the recorded nights (S3).

Description: The simulated telescope should behave like the user's telescope.
This package reads what the recorded nights say about the equipment, through
the libraries' public API, and turns each measurement into the setting of an
INDI simulator property or a PHD2 profile value (section 5 of the simulator
plan):

* `measurements`: asks a `Wayfinder` (opened on the world's library copy) for
  the guide rate, the mount's unguided error, the polar alignment, the Dec
  backlash, the guide controller settings, the star width and the sky rate.
* `world_setup`: the cameras' facts (frame size, gain, bias, read noise,
  clip ceiling) from the world's settings file.
* `driver_model`: the INDI camera simulators' formulas, inverted by hand, so
  no start-up loop of exposures is needed.
* `ranges`: the valid range and unit of each property, from the INDI 2.2.5
  source; every value is clamped to it.
* `fitting`: the conversions and the fallback order of decision D3.
* `results`: the one results file, ``fit_results.json``, with each value's
  source, and the readers the exposure driver and the launcher use.

Run it with ``python -m simulator.fit --root <world>`` (the launcher does this
in a short-lived process, before the backend starts). The real folders are
never opened: the library it reads is the world's own copy, and the process
refuses any other (`simulated_calibration.check_library_is_simulators`).
"""
