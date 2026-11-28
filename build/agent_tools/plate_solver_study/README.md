# Plate-solver study

These scripts compare two local plate solvers, astrometry.net's `solve-field` and Siril's own plate solver, on the same frames. A plate solver works out which part of the sky a frame shows: its center, pixel scale (arcseconds per pixel), rotation and parity (whether the image is mirrored). The scripts run both solvers, then judge every solution with the project's own measuring code against Gaia DR3 star positions. They are measurement tools, not part of the app. The results and the choice of solver they led to are in register entry AST-23 (partly measured; see [AST-23](../../../documentation/library_design/validation/astrometry.md#ast-23)).

Terms are defined in the [glossary](../../../documentation/Glossary.md). This document also defines each term the first time it appears.

## What the study does, in order

1. `prep_frames.py` copies the sample frames out of the library and removes their old plate solutions, so each solver starts from the pointing hint alone. The hint is the header's position (`RA`, `DEC`) and scale (`FOCALLEN`, `XPIXSZ`).
2. `run_solvers.py` runs each solver once per frame in four modes: the header hint, no hint (blind), and a position 2 or 5 degrees north of the header position. solve-field gets the command line the app's driver builds. Siril runs headless (`siril-cli`) in offline mode with `platesolve <ra,dec> -focal= -pixelsize= -force -noflip` and its local Gaia catalog.
3. `judge.py` measures each saved solution against Gaia DR3 stars brighter than G = 16, moved to the frame's date by their proper motion. It reports the constant shift and scatter of the matched stars (the check of register entry AST-01), the residual before and after the project's own Gaia refit, and that residual as a fraction of the star width (the limit of register entry AST-04). It also records the Siril minus solve-field differences of center, scale, rotation and parity.
4. `centering_timing.py` times ten hinted solves in a row, as mount centering runs them: Siril in one open session, Siril in ten separate runs, and solve-field in ten separate runs.
5. `extras.py` runs Siril with a wrong focal length, with the near search turned off at a wrong position, with a far position and a wide near search, with `-downscale`, and twice with the same command to check that a repeat gives the same solution.
6. `summarize.py` joins the runs and judgements into `results.csv` and `results.json` and prints the numbers the register entry quotes.
7. `corner_check.py` (optional) prints, for chosen frames, how far each solver puts the stars from Gaia near the center and near the edge of the field. It writes no file.

`study_common.py` holds the folder layout and the safety checks every script shares.

## Safety

- Every script writes only inside the study folder given with `--study`. It refuses a study folder that lies inside the library named by `prep_frames.py --library` (recorded in the manifest), or inside any folder that holds both a `lights` and a `stacks` folder, the layout of a library.
- `prep_frames.py` opens the library frames read-only (`memmap=False`) and writes stripped copies into `<study>/frames/`.
- Siril always reads a private settings file, `<study>/work/siril_study.ini`, passed with `-i`. `run_solvers.py --siril-settings-source <file>` makes it once from a copy of the user's Siril settings and sets `update_default_scale=false`. Without that setting, Siril stores each solve's focal length and pixel size as the user's defaults. The user's own settings file is only read. Every Siril run checks that the private file still turns the setting off.
- `judge.py --gaia-cache <file>` copies the app's Gaia tile cache (`catalog_cache.db` in the `catalogs` folder of the app's scratch folder) into `<study>/work/` and points the project's catalog store at the copy.

## Rerunning the study

The study needs `solve-field` with its index files in `/usr/share/astrometry` (the app's default), Siril 1.4 from Flatpak (`org.siril.Siril`) with its local Gaia astrometry catalog set in the user's Siril settings, and a Gaia tile cache that covers the fields. Run the scripts with the repository's virtual environment:

```bash
cd build/agent_tools/plate_solver_study
STUDY=<a folder outside the library>
../../../.venv/bin/python prep_frames.py --library <library folder> --study "$STUDY"
../../../.venv/bin/python run_solvers.py --study "$STUDY" solve-field
../../../.venv/bin/python run_solvers.py --study "$STUDY" siril --siril-settings-source <the user's Siril .ini>
../../../.venv/bin/python judge.py --study "$STUDY" --gaia-cache <the app's catalog_cache.db>
../../../.venv/bin/python centering_timing.py --study "$STUDY"
../../../.venv/bin/python extras.py --study "$STUDY"
../../../.venv/bin/python summarize.py --study "$STUDY"
```

A solve-field run with a wrong hint can take up to its 300 s limit, so the `wrong5` mode of solve-field can be run on a few frames only (`run_solvers.py --study "$STUDY" solve-field <ids...> --modes wrong5`). The sample is the `SAMPLE` list in `prep_frames.py`; edit it to study other frames.

## Outputs

All in `<study>/out/`:

| File | What it holds |
| --- | --- |
| `manifest.json` | Each frame's hint cards, source path and library |
| `library_wcs/<id>.hdr` | The library copy's old plate solution, as a third reference |
| `runs.jsonl` | One line per solver run: success, wall-clock and CPU seconds, the solver's own fit numbers |
| `wcs/<solver>/<id>__<mode>.hdr` | Each saved solution |
| `logs/` | Each run's full output |
| `judge.jsonl` | One line per solution: geometry, the AST-01 numbers, the refit numbers and the star-width fractions |
| `compare.jsonl` | Siril minus solve-field differences per frame and mode |
| `centering_timing.json`, `extras.json` | The timing and extra Siril cases |
| `results.csv`, `results.json` | The joined table |

For exact behavior, read the code.
