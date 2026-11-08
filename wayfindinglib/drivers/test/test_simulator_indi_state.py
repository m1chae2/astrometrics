"""Check the INDI simulator has the state its inherited methods use.

The simulator does not call ``IndiInterface.__init__`` (it must not open a
socket), so these tests guard against it missing plain state that the
backend's telemetry loop reads every cycle.
"""

from wayfindinglib.drivers.indi_interface import IndiInterface
from wayfindinglib.drivers.simulators.indi_simulator import SimulatorIndiInterface


def test_simulator_telemetry_drains_do_not_raise() -> None:
    """The drain methods the telemetry loop calls work on the simulator."""
    simulator = SimulatorIndiInterface()
    assert simulator.drain_external_syncs() == []
    assert simulator.drain_polar_alignment() is None


def test_simulator_has_all_runtime_state_of_real_interface() -> None:
    """Every attribute the shared state helper sets exists on the simulator."""
    reference = IndiInterface.__new__(IndiInterface)
    reference._init_runtime_state()
    simulator = SimulatorIndiInterface()
    missing = [name for name in vars(reference) if not hasattr(simulator, name)]
    assert missing == []


def test_simulator_can_abort_both_cameras() -> None:
    """The simulator answers the abort commands the real session offers."""
    simulator = SimulatorIndiInterface()
    assert simulator.abort_main_exposure() is True
    assert simulator.abort_guide_exposure() is True
