:orphan:

.. ==============================================================================
.. Purpose: Overview of the desktop app's documentation.
.. The desktop app pages each sit in the site section that matches their kind
.. (tutorial, how-to, reference, explanation), so this page is not in a toctree.
.. It stays at this path because older links and the app's built-in viewer
.. point here. backend/services/infrastructure/api_doc_generator.py builds the
.. viewer's own copy of this index; keep the two in step.
.. ==============================================================================

Desktop app
===========

The Astrometrics desktop app captures and analyzes images of the night sky. It gives you a visual way to control observatory equipment and to process astronomy images.

.. note::
   The app handles image processing and basic telescope control. For planning and running whole automated observing nights, use a dedicated INDI client such as `KStars/Ekos <https://docs.kde.org/trunk5/en/kstars/kstars/ekos.html>`_.

The desktop app's pages:

- :doc:`/Getting_Started`: a first session with the app.
- :doc:`user_guides/Image_Processing_and_Analysis_Tutorial`: one guided path from raw frames to a measured result.
- :doc:`user_guides/Observatory_Control_and_Automation_Guide`: recipes for connecting and running the telescope.
- :doc:`user_guides/User_Manual`: every view and control of the app.
- :doc:`technical_reference/User_Interface_Architecture`: how the app is built.
