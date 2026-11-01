.. ==============================================================================
.. Purpose: API Reference documentation for astrometricslib using automodapi.
.. One section for the package root and one for each public module
.. (exceptions, reports, jobs, drivers), then the fields of the results the
.. calls return. The results are built by the library-result-fields directive
.. (build/documentation/library_result_fields.py).
.. ==============================================================================

Astrometrics Library (``astrometricslib``)
===========================================

Overview
--------

``astrometricslib`` provides astronomical image processing, calibration, plate
solving, photometry, spectroscopy, and moving object detection pipelines.

The primary entry point is the :class:`~astrometricslib.Astrometrics` façade class.
Instantiate it with an :class:`~astrometricslib.AppConfiguration` to access all
sub-APIs. The package root and four modules are public. Everything else is
private, and only ``astrometricslib`` itself may import it.

.. code-block:: python

   from astrometricslib import Astrometrics, get_configuration

   config = get_configuration()
   astrometrics = Astrometrics(config)

Compositional sub-APIs
----------------------

The :class:`~astrometricslib.Astrometrics` instance provides access to six specialized sub-APIs:

* :attr:`~astrometricslib.Astrometrics.targets` (:class:`~astrometricslib.TargetCatalog`):
  Query, inspect, and manage celestial observation targets and their associated FITS frames.
* :attr:`~astrometricslib.Astrometrics.stars` (:class:`~astrometricslib.StellarCatalog`):
  Query SIMBAD and Gaia DR3/XP stellar databases, cross-match field stars, and analyze light curves.
* :attr:`~astrometricslib.Astrometrics.processing` (:class:`~astrometricslib.ProcessingPipelines`):
  Image stacking and master calibration frame matching (:attr:`~astrometricslib.ProcessingPipelines.calibration`).
* :attr:`~astrometricslib.Astrometrics.quality` (:class:`~astrometricslib.QualityRecords`):
  Stored quality measurements, the list of quality metrics, and live checks of raw frames and stacks.
* :attr:`~astrometricslib.Astrometrics.visualization` (:class:`~astrometricslib.Visualization`):
  Render high-dynamic-range astronomical previews, apply stretching algorithms, and compute coordinate overlays.
* :attr:`~astrometricslib.Astrometrics.jobs` (:class:`~astrometricslib.jobs.Jobs`):
  Monitor, inspect, and query asynchronous pipeline execution jobs.

Core domain models
------------------

The sub-APIs operate on unified observational domain models:

* :class:`~astrometricslib.Target`: Central entity representing a celestial target, its exposure frames, and stack results.
* :class:`~astrometricslib.StellarObject`: Astrometric, photometric, and spectroscopic measurements for a single star.
* :class:`~astrometricslib.FrameRecord`: Metadata and measured quality metrics for an individual FITS exposure.

What to import from where
-------------------------

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Import from
     - What it holds
   * - ``astrometricslib``
     - The entry point and setup, the six sub-API classes, and the core records
       ``Target``, ``FrameRecord``, ``StellarObject`` and ``FilterType``.
   * - ``astrometricslib.exceptions``
     - The errors a script catches, ``ErrorInfo`` with its two converters, and ``FITS_READ_ERRORS``.
   * - ``astrometricslib.reports``
     - The quality reports and row shapes that checks return, ``CaptureSetting`` and ``observing_night_id``.
   * - ``astrometricslib.jobs``
     - The ``Jobs`` class, the job store and records, and the tools that run a function as a tracked background job.
   * - ``astrometricslib.drivers``
     - The data store and file tools that other apps share.

The package root
----------------

.. automodapi:: astrometricslib
   :no-inheritance-diagram:
   :include-all-objects:

Errors: ``astrometricslib.exceptions``
--------------------------------------

.. automodapi:: astrometricslib.exceptions
   :no-inheritance-diagram:
   :include-all-objects:

Reports: ``astrometricslib.reports``
------------------------------------

.. automodapi:: astrometricslib.reports
   :no-inheritance-diagram:
   :include-all-objects:

Jobs: ``astrometricslib.jobs``
------------------------------

.. automodapi:: astrometricslib.jobs
   :no-inheritance-diagram:
   :include-all-objects:

Storage and file tools: ``astrometricslib.drivers``
---------------------------------------------------

.. automodapi:: astrometricslib.drivers
   :no-inheritance-diagram:
   :include-all-objects:

Result fields
-------------

A call returns a result model. The model classes are private, so this section
shows no import path: read the fields by name from what the call returns. For
example, ``astrometrics.targets.query(detail="cameras")`` returns an object
whose ``cameras`` field is a dictionary of camera names and frame counts. A
result that the package root or one of the modules above documents is not
repeated here.

.. library-result-fields:: astrometricslib
