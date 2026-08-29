.. ==============================================================================
.. Purpose: Main entry point index file for Astrometrics Sphinx documentation portal.
.. Provides landing hero, grid card links, and consolidated navbar toctrees.
.. Three navbar dropdowns: Introduction, Tutorials, API.
.. ==============================================================================

Astrometrics Documentation
==========================

Welcome to the Astrometrics documentation.

Astrometrics is a desktop application for astrophotography, photometry, spectroscopy, and automated observatory control. It provides a complete set of tools to capture and process images of the night sky.

.. grid:: 1 2 2 3
    :gutter: 3

    .. grid-item-card:: Installation
        :link: Installation
        :link-type: doc

        Learn how to install Astrometrics and its requirements on your computer.

    .. grid-item-card:: Getting Started
        :link: Getting_Started
        :link-type: doc

        Core concepts, hardware requirements, and launching the application.

    .. grid-item-card:: Desktop Application
        :link: user_interface/index
        :link-type: doc

        User manual and step-by-step guides for learning how to use the application.

For users with a technical background looking to perform customized data processing or automate hardware outside of the desktop interface, the underlying Python libraries can be imported directly:

.. grid:: 1 2 2 2
    :gutter: 3

    .. grid-item-card:: Interactive Tutorials
        :link: notebooks/index
        :link-type: doc

        Step-by-step Jupyter Notebook guides covering FITS frame calibration, star detection, photometry, spectroscopy, and telescope control.

    .. grid-item-card:: Python API Reference
        :link: api/index
        :link-type: doc

        Complete API reference for the astrometricslib and wayfindinglib Python packages.

For users interested in understanding the mathematical models and logic powering the application, the algorithm architecture is documented below:

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

.. toctree::
   :maxdepth: 2
   :hidden:

   Installation <Installation>
   Getting Started <Getting_Started>
   Desktop App <user_interface/index>
   Tutorials <notebooks/index>
   API <api/index>
   Image Processing Architecture <library_design/Astrometrics_Library_Architecture>
   Wayfinding Architecture <library_design/Wayfinding_Library_Architecture>
   Image Processing Implementation <library_design/Astrometrics_Library_Implementation>
   Wayfinding Implementation <library_design/Wayfinding_Library_Implementation>
