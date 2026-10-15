.. ==============================================================================
.. Purpose: Main entry point of the Astrometrics Sphinx documentation portal.
.. Provides the landing page with one row of cards per documentation group, and
.. the captioned toctrees that build the navigation bar and sidebar. The groups
.. are Get started, Tutorials, How-to guides, Explanation, Reference, Evidence
.. and About the docs. Every page must be in a toctree (sphinx-build -W).
.. ==============================================================================

Astrometrics Documentation
==========================

Welcome to the Astrometrics documentation.

Astrometrics is a desktop application for astrophotography, photometry, spectroscopy, and automated observatory control. It provides a complete set of tools to capture and process images of the night sky. The documentation is grouped by what you want to do. :doc:`About_These_Docs` maps every document.

Get started
-----------

.. grid:: 1 2 2 3
    :gutter: 3

    .. grid-item-card:: Installation
        :link: Installation
        :link-type: doc

        Install Astrometrics and its requirements on your computer.

    .. grid-item-card:: Getting Started
        :link: Getting_Started
        :link-type: doc

        Core concepts, hardware requirements, and launching the application.

    .. grid-item-card:: Library quick start
        :link: how_to/library_quick_start
        :link-type: doc

        Install the Python library, set up the configuration file, and make a first call.

Tutorials
---------

.. grid:: 1 2 2 2
    :gutter: 3

    .. grid-item-card:: Desktop application
        :link: user_interface/index
        :link-type: doc

        User manual and step-by-step guides for learning how to use the application.

    .. grid-item-card:: Interactive tutorials
        :link: notebooks/index
        :link-type: doc

        Step-by-step Jupyter Notebook guides covering FITS frame calibration, star detection, photometry, spectroscopy, and telescope control.

How-to guides
-------------

.. grid:: 1 2 2 3
    :gutter: 3

    .. grid-item-card:: Tune for your setup
        :link: how_to/tune_for_your_setup
        :link-type: doc

        An ordered checklist for checking the tuned limits on your own camera and telescope.

    .. grid-item-card:: Validate on your data
        :link: how_to/validate_on_your_data
        :link-type: doc

        Run a validation-register procedure on your own frames.

    .. grid-item-card:: Change a tuned value
        :link: how_to/change_a_tuned_value
        :link-type: doc

        Update the code, the register and the guides together.

    .. grid-item-card:: Add a register entry
        :link: how_to/add_a_register_entry
        :link-type: doc

        Record the evidence for a tuned value.

    .. grid-item-card:: Add a quality gate
        :link: how_to/add_a_quality_gate
        :link-type: doc

        Add a gate so the review guides and tests stay in step.

Explanation
-----------

.. grid:: 1 2 2 2
    :gutter: 3

    .. grid-item-card:: Image Processing Architecture
        :link: library_design/Astrometrics_Library_Architecture
        :link-type: doc

        Theoretical framework and mathematical derivations for astrometry, photometry, and spectroscopy.

    .. grid-item-card:: Wayfinding Architecture
        :link: library_design/Wayfinding_Library_Architecture
        :link-type: doc

        Logic models for target selection, INDI hardware abstraction, and dynamic observation planning.

    .. grid-item-card:: Pipeline review guides
        :link: library_design/pipelines/index
        :link-type: doc

        One guide per pipeline for scientific review: inputs, math, flow, tuned limits, fallbacks and quality gates.

    .. grid-item-card:: Desktop application architecture
        :link: user_interface/technical_reference/User_Interface_Architecture
        :link-type: doc

        How the desktop application is built: data flow, state, rendering and the backend interface.

Reference
---------

.. grid:: 1 2 2 3
    :gutter: 3

    .. grid-item-card:: Python API reference
        :link: api/index
        :link-type: doc

        Complete API reference for the astrometricslib and wayfindinglib Python packages.

    .. grid-item-card:: Image Processing Implementation
        :link: library_design/Astrometrics_Library_Implementation
        :link-type: doc

        Code names, tuned values and their status labels.

    .. grid-item-card:: Wayfinding Implementation
        :link: library_design/Wayfinding_Library_Implementation
        :link-type: doc

        Code names and settings for the observatory control library.

Evidence
--------

.. grid:: 1 2 2 2
    :gutter: 3

    .. grid-item-card:: Validation register
        :link: library_design/validation/index
        :link-type: doc

        How each tuned value was checked: data, library calls, results and how to repeat them.

    .. grid-item-card:: Pipeline audit report
        :link: library_design/Pipeline_Audit_Report
        :link-type: doc

        A generated check that the architecture paper, the implementation paper and the code agree.

About the docs
--------------

.. grid:: 1 2 2 2
    :gutter: 3

    .. grid-item-card:: About these docs
        :link: About_These_Docs
        :link-type: doc

        A map of every document: its kind, audience, and what checks it.

    .. grid-item-card:: Documentation conventions
        :link: Documentation_Conventions
        :link-type: doc

        Where content lives, document layouts, spelling, terms and units.

.. toctree::
   :maxdepth: 2
   :caption: Get started
   :hidden:

   Installation <Installation>
   Getting Started <Getting_Started>
   Library Quick Start <how_to/library_quick_start>

.. toctree::
   :maxdepth: 2
   :caption: Tutorials
   :hidden:

   Desktop App <user_interface/index>
   Notebook Tutorials <notebooks/index>

.. toctree::
   :maxdepth: 2
   :caption: How-to guides
   :hidden:

   Tune for Your Setup <how_to/tune_for_your_setup>
   Validate on Your Data <how_to/validate_on_your_data>
   Change a Tuned Value <how_to/change_a_tuned_value>
   Add a Register Entry <how_to/add_a_register_entry>
   Add a Quality Gate <how_to/add_a_quality_gate>

.. toctree::
   :maxdepth: 2
   :caption: Explanation
   :hidden:

   Image Processing Architecture <library_design/Astrometrics_Library_Architecture>
   Wayfinding Architecture <library_design/Wayfinding_Library_Architecture>
   Pipeline Review Guides <library_design/pipelines/index>
   Desktop App Architecture <user_interface/technical_reference/User_Interface_Architecture>

.. toctree::
   :maxdepth: 2
   :caption: Reference
   :hidden:

   API <api/index>
   Image Processing Implementation <library_design/Astrometrics_Library_Implementation>
   Wayfinding Implementation <library_design/Wayfinding_Library_Implementation>

.. toctree::
   :maxdepth: 2
   :caption: Evidence
   :hidden:

   Validation Register <library_design/validation/index>
   Pipeline Audit Report <library_design/Pipeline_Audit_Report>

.. toctree::
   :maxdepth: 2
   :caption: About the docs
   :hidden:

   About These Docs <About_These_Docs>
   Documentation Conventions <Documentation_Conventions>
