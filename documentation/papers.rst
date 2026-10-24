:orphan:

.. ==============================================================================
.. Purpose: Starting page of the PDF download.
.. The PDF holds the five papers only: each library's architecture paper
.. (theory) and implementation paper (code names and settings), and the
.. Astrometrics quality measurements paper (theory of the measurements). The rest of the
.. site (API reference, register, guides) is meant to be searched online, not
.. printed. conf.py points latex_documents here and leaves this page out of the
.. HTML build, so it never appears on the website.
.. ==============================================================================

Astrometrics papers
===================

.. toctree::
   :maxdepth: 2

   library_design/Astrometrics_Library_Architecture
   library_design/Astrometrics_Quality_Measurements
   library_design/Astrometrics_Library_Implementation
   library_design/Wayfinding_Library_Architecture
   library_design/Wayfinding_Library_Implementation
