.. ==============================================================================
.. Purpose: Introduction hub page for Astrometrics documentation.
.. Groups orientation and architectural reference documents under a single
.. top-level navbar entry. Sub-pages appear in the left sidebar.
.. ==============================================================================

Introduction
============

This section covers everything you need to get started with Astrometrics and understand its architecture.

.. grid:: 1 2 2 2
    :gutter: 3

    .. grid-item-card:: Installation
        :link: ../Installation
        :link-type: doc

        System dependencies, Python requirements, platform setup, configuration, and how to verify a working install.

    .. grid-item-card:: Getting Started
        :link: ../Getting_Started
        :link-type: doc

        A quick-start guide to both libraries: learn how to register an observation target, process its images, and plan an observation session.

    .. grid-item-card:: Astrometrics Library Architecture
        :link: ../library_design/Astrometrics_Library_Architecture
        :link-type: doc

        Domain architecture, component relationships, and design decisions for the ``astrometricslib`` image processing library.

    .. grid-item-card:: Wayfinding Library Architecture
        :link: ../library_design/Wayfinding_Library_Architecture
        :link-type: doc

        Domain architecture, component relationships, and design decisions for the ``wayfindinglib`` observatory control library.


.. toctree::
   :maxdepth: 2
   :hidden:

   ../Installation
   ../Getting_Started
   ../library_design/Astrometrics_Library_Architecture
   ../library_design/Wayfinding_Library_Architecture
