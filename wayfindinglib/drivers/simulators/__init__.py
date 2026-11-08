"""Purpose: In-process hardware simulators for wayfindinglib drivers.

Description: Not test files -- these are real, importable driver
implementations (e.g. `SimulatorIndiInterface`, in
`wayfindinglib.drivers.simulators.simulator_indi_interface`) used by the
test suite. The interactive simulation mode does not use them: it runs the
real INDI simulators through `indiserver` (see `simulator/README.md`). Kept
separate from `drivers/test/`, which holds actual pytest test cases.
"""
