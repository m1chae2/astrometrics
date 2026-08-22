.. ==============================================================================
.. Purpose: Main entry point index file for Astrometrics Sphinx documentation portal.
.. Provides landing hero, grid card links, and consolidated navbar toctrees.
.. Three navbar dropdowns: Introduction, Tutorials, API.
.. ==============================================================================

Astrometrics Documentation
==========================

Welcome to the **Astrometrics** scientific image processing and **Wayfinding** observatory navigation library documentation.

Astrometrics consists of two Python libraries and a desktop application, providing a complete ecosystem for astrophotography, precision photometry, slitless spectroscopy, and automated observatory control. Built with a dual-interface architecture, every capability available in the desktop app is also fully scriptable via the Python libraries, allowing you to seamlessly transition from interactive exploration to fully automated workflows.

.. grid:: 1 2 2 2
    :gutter: 3

    .. grid-item-card:: Installation
        :link: Installation
        :link-type: doc

        System dependencies, Python requirements, platform setup, configuration, and how to verify a working install.

    .. grid-item-card:: Desktop Application
        :link: user_interface/index
        :link-type: doc

        Official handbook, observation guides, and technical reference for the desktop observatory application.

    .. grid-item-card:: Getting Started
        :link: Getting_Started
        :link-type: doc

        A quick-start guide to both libraries: learn how to register an observation target, process its images, and plan an observation session.

    .. grid-item-card:: Interactive Tutorials
        :link: notebooks/index
        :link-type: doc

        Step-by-step Jupyter Notebook guides covering FITS frame calibration, star detection, photometry, spectroscopy, and telescope control.

    .. grid-item-card:: Astrometrics Library API
        :link: api/astrometricslib
        :link-type: doc

        Complete API reference for image calibration, plate solving, star field visualization, butler storage access, and spectroscopy pipelines.

    .. grid-item-card:: Wayfinding Library API
        :link: api/wayfindinglib
        :link-type: doc

        Complete API reference for INDI mount control, target visibility calculations, mosaic planning, and observation execution.

.. ---------------------------------------------------------------------------
.. Root toctree: exactly four entries → four header navbar items.
.. Each hub page owns its own toctree so sub-pages appear in the sidebar.
.. ---------------------------------------------------------------------------
.. toctree::
   :maxdepth: 2
   :hidden:

   Introduction <introduction/index>
   Desktop App <user_interface/index>
   Tutorials <notebooks/index>
   API <api/index>
