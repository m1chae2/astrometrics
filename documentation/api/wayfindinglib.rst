.. ==============================================================================
.. Purpose: API Reference documentation for wayfindinglib using automodapi.
.. One section for the package root and each public module, one for each
.. sub-API, then the fields of the results the calls return (built by the
.. library-result-fields directive, build/documentation/library_result_fields.py).
.. ==============================================================================

Wayfinding Library (``wayfindinglib``)
======================================

Overview
--------

``wayfindinglib`` provides observatory automation, hardware control via the
INDI protocol, observation planning, and unattended night session execution.

The primary entry point is the :class:`~wayfindinglib.Wayfinder` façade class.
Instantiate it with an :class:`~astrometricslib.AppConfiguration` to access all
four sub-APIs:

.. code-block:: python

   from astrometricslib import get_configuration
   from wayfindinglib import Wayfinder

   config = get_configuration()
   wayfinder = Wayfinder(config)

Compositional sub-APIs
----------------------

The :class:`~wayfindinglib.Wayfinder` instance exposes four peer sub-APIs:

* :attr:`~wayfindinglib.Wayfinder.control` (:class:`~wayfindinglib.ObservatoryControl`):
  Direct hardware operation through six children: ``mount``, ``imaging``,
  ``guiding``, ``safety``, ``equipment`` and ``remote``.
* :attr:`~wayfindinglib.Wayfinder.planning` (:class:`~wayfindinglib.ObservationPlanning`):
  Astronomical target visibility, observation packages, sequence
  construction, and mosaic grid generation.
* :attr:`~wayfindinglib.Wayfinder.execution` (:class:`~wayfindinglib.ObservationExecution`):
  Automated night queue execution, divergence tracking, and session telemetry
  recording.
* :attr:`~wayfindinglib.Wayfinder.review` (``ObservationReview``):
  Past and current observing nights, alignment sessions, and the limits the
  equipment has shown.

What to import from where
-------------------------

The package root and two modules are public. Everything else is private, and
only ``wayfindinglib`` itself may import it.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Import from
     - What it holds
   * - ``wayfindinglib``
     - ``Wayfinder``, the three sub-API classes, ``ObservationSession`` and ``ObservationPackage``.
   * - ``wayfindinglib.exceptions``
     - The errors that belong to this library. The shared categories come from ``astrometricslib.exceptions``.
   * - ``wayfindinglib.reports``
     - The states and levels that results report.

The package root
----------------

.. automodapi:: wayfindinglib
   :no-inheritance-diagram:

Errors: ``wayfindinglib.exceptions``
------------------------------------

.. automodapi:: wayfindinglib.exceptions
   :no-inheritance-diagram:

Reports: ``wayfindinglib.reports``
----------------------------------

.. automodapi:: wayfindinglib.reports
   :no-inheritance-diagram:

Observatory control API
-----------------------

.. automodapi:: wayfindinglib.api.control.mount
   :no-inheritance-diagram:

.. automodapi:: wayfindinglib.api.control.imaging
   :no-inheritance-diagram:

.. automodapi:: wayfindinglib.api.control.guiding
   :no-inheritance-diagram:

.. automodapi:: wayfindinglib.api.control.remote
   :no-inheritance-diagram:

.. automodapi:: wayfindinglib.api.review
   :no-inheritance-diagram:

.. automodapi:: wayfindinglib.api.control.safety
   :no-inheritance-diagram:

.. automodapi:: wayfindinglib.api.control.equipment
   :no-inheritance-diagram:

Observation planning API
------------------------

.. automodapi:: wayfindinglib.api.planning
   :no-inheritance-diagram:

Observation execution API
-------------------------

.. automodapi:: wayfindinglib.api.execution
   :no-inheritance-diagram:

Result fields
-------------

A call returns a result model. The model classes are private, so this section
shows no import path: read the fields by name from what the call returns. For
example, ``wayfinder.planning.lookup_coordinates("M 31")`` returns an object
with ``ra_deg`` and ``dec_deg`` fields. A result that the package root or one
of the modules above documents is not repeated here.

.. library-result-fields:: wayfindinglib
