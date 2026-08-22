.. ==============================================================================
.. Purpose: Main entry point index file for Astrometrics Sphinx documentation portal.
.. Provides landing hero, grid card links, and consolidated navbar toctrees.
.. Three navbar dropdowns: Introduction, Tutorials, API.
.. ==============================================================================

Astrometrics Documentation
==========================

Welcome to the **Astrometrics** scientific image processing and **Wayfinding** observatory navigation library documentation.

Astrometrics is a clean, modern Python domain ecosystem and visualization platform for astrophotography, precision photometry, slitless spectroscopy, and automated observatory control. Designed with a dual-interface architecture, every capability available in the Desktop Application is also fully scriptable via the core Python libraries, allowing you to seamlessly transition from interactive exploration to fully automated workflows.

.. grid:: 1 2 2 2
    :gutter: 3

    .. grid-item-card:: Installation
        :link: Installation
        :link-type: doc

        System dependencies, Python requirements, platform setup, configuration, and how to verify a working install.

    .. grid-item-card:: Desktop Application
        :link: user_interface/index
        :link-type: doc

        Official handbook, observation guides, and technical reference for the Electron/Vite desktop observatory interface.

    .. grid-item-card:: Getting Started
        :link: Getting_Started
        :link-type: doc

        A first worked sequence across both libraries: registering a target, processing its frames, and planning when to observe it.

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
