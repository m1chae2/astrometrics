# Reference spectra for spectral classification

Thirty-four main-sequence reference spectra used by `spectral_classifier.py`
to guess a star's broad spectral type by comparing an observed spectrum's
shape against these known ones.

## Source

Pickles, A.J. 1998, "A Stellar Spectral Flux Library: 1150-25000 A",
*Publications of the Astronomical Society of the Pacific*, 110, 863
(1998PASP..110..863P). Retrieved from the CDS VizieR archive, catalog
`J/PASP/110/863` (<https://cdsarc.cds.unistra.fr/ftp/J/PASP/110/863/>).

## Processing

Each file here (`<type>.txt`, e.g. `g0v.txt`) is derived from the matching
original `<type>.dat.gz` in the source catalog:

1. Parsed the original fixed-width columns (wavelength in Angstroms, the
   component-averaged normalized flux `nflam`).
2. Trimmed to 3500-8000 A -- the range a visible-light slitless grism
   (e.g. a Star Analyzer) can actually cover, and wide enough to include
   the Balmer series and the overall continuum shape.
3. Kept only wavelength and flux; the original per-source-catalog component
   columns and standard-deviation column aren't needed for classification.

Stored as plain comma-separated `.txt`, not `.csv`: this repo's
`.gitattributes` routes `*.csv` through Git LFS, and CI's checkout doesn't
fetch LFS content, so these small bundled tables need to stay plain blobs.

Flux stays normalized to 1.0 at 5556 A, per the source library's own
convention -- `spectral_classifier.py` re-normalizes both sides to a common
scale before comparing, so this reference point doesn't need to match the
observed spectrum's own calibration.

## Coverage

O5V, O9V, B0V, B1V, B3V, B8V, B9V, A0V, A2V, A3V, A5V, A7V, F0V, F2V, F5V,
F6V, F8V, G0V, G2V, G5V, G8V, K0V, K2V, K3V, K4V, K5V, K7V, M0V, M1V, M2V,
M3V, M4V, M5V, M6V -- every single- or double-subtype rung the Pickles
library offers for solar-abundance dwarfs, hottest to coolest. This is
denser than an early version of this set that only sampled every ~5
subtypes: a star whose true type falls between two coarse rungs (e.g.
between K5V and M0V) used to be a near-toss-up between two templates each
some distance away, showing up as an `is_classification_ambiguous` flag
in the quality summary. With every rung present, that same star usually
has one clearly-closer template to land on.

The full Pickles library also has metal-weak/metal-rich variants of
several F-K dwarf types (e.g. `wg5v.dat`, `rk0v.dat`) and giant/supergiant
luminosity classes; those aren't bundled here; two library types are also
skipped that already fall on the seams of the standard rungs above
(`b57v.dat`, a merged B5-7V spectrum, and `m2p5v.dat`, halfway between M2V
and M3V). None of that adds resolving power for this classifier's actual
job -- placing an unknown dwarf star on the O-B-A-F-G-K-M sequence -- and
would just mean more reference correlations to compute per star.
