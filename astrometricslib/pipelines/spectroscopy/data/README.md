# Reference spectra for spectral classification

Thirteen main-sequence reference spectra used by `spectral_classifier.py` to
guess a star's broad spectral type by comparing an observed spectrum's shape
against these known ones.

## Source

Pickles, A.J. 1998, "A Stellar Spectral Flux Library: 1150-25000 A",
*Publications of the Astronomical Society of the Pacific*, 110, 863
(1998PASP..110..863P). Retrieved from the CDS VizieR archive, catalog
`J/PASP/110/863` (<https://cdsarc.cds.unistra.fr/ftp/J/PASP/110/863/>).

## Processing

Each file here (`<type>.csv`, e.g. `g0v.csv`) is derived from the matching
original `<type>.dat.gz` in the source catalog:

1. Parsed the original fixed-width columns (wavelength in Angstroms, the
   component-averaged normalized flux `nflam`).
2. Trimmed to 3500-8000 A -- the range a visible-light slitless grism
   (e.g. a Star Analyzer) can actually cover, and wide enough to include
   the Balmer series and the overall continuum shape.
3. Kept only wavelength and flux; the original per-source-catalog component
   columns and standard-deviation column aren't needed for classification.

Flux stays normalized to 1.0 at 5556 A, per the source library's own
convention -- `spectral_classifier.py` re-normalizes both sides to a common
scale before comparing, so this reference point doesn't need to match the
observed spectrum's own calibration.

## Coverage

O5V, B0V, B8V, A0V, A5V, F0V, F5V, G0V, G5V, K0V, K5V, M0V, M5V -- a
representative main-sequence ladder from hottest to coolest. The full
Pickles library has 131 spectra covering more subtypes and luminosity
classes; this subset was chosen to keep the bundled data small while still
spanning the full O-B-A-F-G-K-M sequence.
