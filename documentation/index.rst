.. ==============================================================================
.. Purpose: Main entry point of the Astrometrics Sphinx documentation portal.
.. Shows one card per documentation section and holds the root toctree. The
.. root toctree lists only the seven section pages (get_started, tutorials,
.. how_to/index, explanation, reference, evidence, about), so the top
.. navigation bar shows the sections and each section page lists its own pages.
.. Every page must be in exactly one toctree (sphinx-build -W and
.. astrometricslib/test/test_documentation_site.py).
.. ==============================================================================

Astrometrics documentation
==========================

Astrometrics is a desktop application and a pair of Python libraries for astrophotography, photometry, spectroscopy and observatory control. It captures images of the night sky, processes them into measurements, and records how reliable each measurement is.

The documentation is grouped by what you want to do. :doc:`About_These_Docs` maps every document.

Start by goal
-------------

.. grid:: 1 1 3 3
    :gutter: 3

    .. grid-item-card:: Using the desktop app
        :link: Getting_Started
        :link-type: doc

        Set up and operate the telescope application:

        * :doc:`Installation`
        * :doc:`Getting_Started`
        * :doc:`user_interface/user_guides/User_Manual`
        * :doc:`user_interface/user_guides/Observatory_Control_and_Automation_Guide`

    .. grid-item-card:: Developing with the codebase
        :link: how_to/library_quick_start
        :link-type: doc

        Call or extend the Python libraries:

        * :doc:`how_to/library_quick_start`
        * :doc:`library_design/Astrometrics_Library_Implementation`
        * :doc:`library_design/Wayfinding_Library_Implementation`
        * :doc:`api/index`

    .. grid-item-card:: Scientific methods and evidence
        :link: library_design/Astrometrics_Pipeline_Dataflow
        :link-type: doc

        Explore the mathematics and validation:

        * :doc:`library_design/Astrometrics_Pipeline_Dataflow`
        * :doc:`library_design/Astrometrics_Library_Architecture`
        * :doc:`library_design/Wayfinding_Quality_Judgment`
        * :doc:`library_design/validation/index`

Explore by section
------------------

.. grid:: 1 2 2 3
    :gutter: 3

    .. grid-item-card:: Get started
        :link: get_started
        :link-type: doc

        Install the application, take a first tour of the desktop app, or make a first call to the Python library.

    .. grid-item-card:: Tutorials
        :link: tutorials
        :link-type: doc

        Learn by doing: one guided path through image processing, and runnable notebooks.

    .. grid-item-card:: How-to guides
        :link: how_to/index
        :link-type: doc

        Recipes for one task each: control the observatory, tune the processing for your setup, change a tuned value.

    .. grid-item-card:: Explanation
        :link: explanation
        :link-type: doc

        How the software works and why: the theory of each library, and how the desktop app is built.

    .. grid-item-card:: Reference
        :link: reference
        :link-type: doc

        Look things up: the Python API, the user manual, the pipeline review guides and the implementation papers.

    .. grid-item-card:: Evidence
        :link: evidence
        :link-type: doc

        How each tuned value was checked, and a generated check that the documents agree with the code.

    .. grid-item-card:: About the docs
        :link: about
        :link-type: doc

        How the documentation is organized and the rules it follows.

.. toctree::
   :hidden:

   get_started
   tutorials
   how_to/index
   explanation
   reference
   evidence
   about
