.. ==============================================================================
.. Purpose: API Reference documentation for wayfindinglib using automodapi.
.. One section for the package root and each public module, one for each
.. sub-API, then the fields of the results the calls return (built by the
.. library-result-fields directive, build/documentation/library_result_fields.py).
.. ==============================================================================

Wayfinding Library (``wayfindinglib``)
======================================

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

.. library-result-fields:: wayfindinglib
