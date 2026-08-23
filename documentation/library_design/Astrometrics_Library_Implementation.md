# Astrometrics Library Implementation Map

While the theoretical algorithms and data flow are covered in the [Astrometrics Library Architecture](./Astrometrics_Library_Architecture.md) document, this map serves as a direct index to the Python source code where those algorithms are physically implemented.

Due to the internal nature of these modules, they are deliberately hidden from the public API Reference. Developers wishing to review or modify the core algorithms should refer to the following directories within the `astrometricslib/tasks/` package:

## Core Processing Tasks

### Stacking and Calibration
*Located in:* `astrometricslib/tasks/target_tasks/`
- **Frame calibration:** `pipeline_tasks.py` and `batch_processing_tasks.py`
- **Sub-exposure quality evaluation:** `frame_homogeneity.py` and `background_homogeneity_tasks.py`
- **Star alignment and registration:** `spectral_registration_quality.py`
- **Pixel rejection and integration:** `rejection_thresholds.py` and `stacking_tasks.py`

### Astrometry
*Located in:* `astrometricslib/tasks/stellar_tasks/astrometry_tasks/`
- **Plate solving:** `plate_solver.py`
- **Star detection and centroiding:** `star_identifier.py`
- **Coordinate transformations (WCS):** `astrometry_pipeline.py`

### Photometry
*Located in:* `astrometricslib/tasks/stellar_tasks/photometry_tasks/`
- **Aperture photometry:** `variability_analyzer.py`
- **PSF modeling:** `variability_analyzer.py`
- **Light curve generation:** `variability_analyzer.py`

### Spectroscopy
*Located in:* `astrometricslib/tasks/stellar_tasks/spectroscopy_tasks/`
- **Spectrum extraction:** `spectrum_extractor.py` and `_extractor_c.c`
- **Wavelength calibration:** `spectrum_calibrator.py` and `calibration_tuner.py`
- **Flux calibration:** `quantum_efficiency_correction.py` and `optics_physics.py`

### Moving Object Detection
*Located in:* `astrometricslib/tasks/moving_object_tasks/`
- **Blink analysis and tracking:** `moving_object_detection_tasks.py` and `moving_object_pipeline_tasks.py`
- **Ephemeris calculation:** `moving_object_ephemeris_tasks.py` and coordinate mapping

### Shared Scientific Utilities
*Located in:* `astrometricslib/tasks/shared/`
- Image math operations (NumPy/SciPy wrappers)
- Coordinate transformations (WCS)
