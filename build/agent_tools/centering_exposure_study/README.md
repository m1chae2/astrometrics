# Centering exposure study

These scripts measure how few stars Siril's plate solver needs to solve a short centering frame, and check the centering exposure calculator against real frames. They are measurement tools, not part of the app. The results are in register entry WAY-77 (see [WAY-77](../../../documentation/library_design/validation/wayfinding.md#way-77)).

Terms are defined in the [glossary](../../../documentation/Glossary.md). A plate solver works out which part of the sky a frame shows. Centering starts it from the header position (`RA`, `DEC`) and scale (`FOCALLEN`, `XPIXSZ`), and calls the app's own Siril solve (`solve_image`).

## What the scripts do

1. `solver_needs.py` takes real library frames (read only) and makes two kinds of weaker copies of each:
   - an exposure copy: the frame as a shorter exposure would have shown it. Each electron is kept with the probability of the exposure ratio (binomial thinning), so the shot noise is right, and the read noise is topped up to its full size;
   - a star copy: a frame of pure sky noise (the frame's own sky level and noise) with only the N brightest detected stars of the real frame pasted in. This separates the number of stars from the depth.

   It counts the stars of each copy with a 5-sigma star finder, solves it with Siril from the header position, and compares the answer with the solution of the full frame. A solve is good when its center is within 3 pixels and its scale within 1 percent. One JSON line per copy goes to `<study>/out/solver_needs.jsonl`.
2. `validate_calculator.py` asks the calculator (`predict_star_counts`, `choose_centering_exposure`) about the same ZWO frames. It compares the predicted star count with the stars found in exposure copies, solves a copy at the exposure the calculator chooses, and solves copies at the shortest exposure where the calculator expects its target count (the margin of the target). It reads the Gaia tile cache copy in place of the deep star catalog, which is not installed on the copies. Results go to `<study>/out/calculator_check.jsonl`.

## Safety

- Both scripts open library frames read only and write only inside the study folder. They refuse a folder that holds a `lights` folder.
- Run them only through `build/agent_tools/realrun.sh` on the copy, with the scripts copied into `ASTROMETRICS_SCRATCH` (see [the agent tools README](../README.md)). `validate_calculator.py` needs `solver_needs.py` beside it and a copy of the Gaia tile cache at `<study>/work/catalog_cache_copy.db`.
- Put the study folder outside `/tmp`, which is a RAM disk.

For exact behavior, read the code.
