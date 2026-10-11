.. ==============================================================================
.. Purpose: API Reference documentation for wayfindinglib using automodapi.
.. Includes top-level facade and api subpackage modules.
.. ==============================================================================

Wayfinding Library (``wayfindinglib``)
======================================

Overview
--------

``wayfindinglib`` provides observatory automation, hardware control via the
INDI protocol, observation planning, and unattended night session execution.

The primary entry point is the :class:`~wayfindinglib.Wayfinder` façade class.
Instantiate it with an :class:`~astrometricslib.AppConfiguration` to access all
three subsystems:

.. code-block:: python

   from astrometricslib import get_configuration
   from wayfindinglib import Wayfinder

   config = get_configuration()
   wayfinder = Wayfinder(config)

Compositional Sub-APIs
----------------------

The :class:`~wayfindinglib.Wayfinder` instance exposes three peer subsystems:

* :attr:`~wayfindinglib.Wayfinder.control` (:class:`~wayfindinglib.api.control.control.ObservatoryControl`):
  Direct hardware operation for telescope mounts, imaging cameras, auto-guiders,
  safety monitors, and observatory equipment.
* :attr:`~wayfindinglib.Wayfinder.planning` (:class:`~wayfindinglib.api.planning.ObservationPlanning`):
  Astronomical target visibility, airmass curves, transit times, observation
  packages, sequence construction, and mosaic grid generation.
* :attr:`~wayfindinglib.Wayfinder.execution` (:class:`~wayfindinglib.api.execution.ObservationExecution`):
  Automated night queue execution, telescope slewing, capture orchestration,
  divergence tracking, and session telemetry recording.

Top-Level Facade and Models
---------------------------

.. automodapi:: wayfindinglib
   :no-inheritance-diagram:
   :skip: IndiInterface
   :skip: SimulatorIndiInterface

Observatory Control API
-----------------------

.. automodapi:: wayfindinglib.api.control.control
   :no-inheritance-diagram:
   :skip: ObservatoryControl

.. automodapi:: wayfindinglib.api.control.mount
   :no-inheritance-diagram:

.. automodapi:: wayfindinglib.api.control.imaging
   :no-inheritance-diagram:

.. automodapi:: wayfindinglib.api.control.guiding
   :no-inheritance-diagram:

.. automodapi:: wayfindinglib.api.control.remote
   :no-inheritance-diagram:

.. automodapi:: wayfindinglib.api.control.history
   :no-inheritance-diagram:

.. automodapi:: wayfindinglib.api.control.safety
   :no-inheritance-diagram:

.. automodapi:: wayfindinglib.api.control.equipment
   :no-inheritance-diagram:

Observation Planning API
------------------------

.. automodapi:: wayfindinglib.api.planning
   :no-inheritance-diagram:

Observation Execution API
-------------------------

.. automodapi:: wayfindinglib.api.execution
   :no-inheritance-diagram:
