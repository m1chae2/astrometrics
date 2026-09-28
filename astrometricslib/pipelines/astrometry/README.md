# Astrometry Pipeline

This pipeline answers two questions about a single image: where in the sky is it pointing, and which stars does it contain? It runs in three stages, in order: pre-processing, processing, and post-processing. Each stage has its own folder and its own README with more detail.

## What the pipeline does, in order

1. **Load the image.** The pipeline accepts either a file path or an already-loaded image.
2. **Pre-process the image.** Find every star-like point of light in the image, and, when needed elsewhere, measure how sharp those points look.
3. **Process the detections.** Match the detected points against the image itself to work out the exact map of the sky (plate solving), then look up each star's real identity in astronomical catalogs.
4. **Post-process each match.** Record how confident the pipeline is in each star's identity, based on how the match was made.
5. **Save the results.** The pipeline hands the stars, their catalog identities, and the solved sky coordinates back to the caller for storage.

## Where each piece lives

- `pipeline.py` runs steps 1 through 4 for a single image. It is the coordinator: it does not detect stars or query catalogs itself, but it calls the pieces that do, in the right order.
- `runner.py` sits one level above `pipeline.py`. It runs the pipeline across a whole observing session, writes the solved sky position back onto the target's record, and produces a summary of how well the run went.
- `pre_processing/`, `processing/`, and `post_processing/` hold the three stages described above.
- `utilities/` holds tools that support astrometry but are not part of running the pipeline on an image — for example, downloading a region of the star catalog ahead of time so a batch of images does not overwhelm the remote database.

## A note on scope

This pipeline identifies stars and solves the sky position of an image. It does not measure star brightness (that is photometry) or star spectra (that is spectroscopy) — those pipelines depend on astrometry's output but live elsewhere.

This README describes the overall flow. For exact behavior, read the code — the code is always the source of truth.
