# Reference spectra for spectral classification

Ninety reference spectra that `spectral_classifier.py` uses to estimate a
star's broad spectral type by comparing an observed spectrum's shape against
these known ones: 34 main-sequence stars and 56 giants, bright giants and
supergiants (see "Giant and supergiant references" below).

## Source

Pickles, A.J. 1998, "A Stellar Spectral Flux Library: 1150-25000 A",
*Publications of the Astronomical Society of the Pacific*, 110, 863
(1998PASP..110..863P). Retrieved from the CDS VizieR archive, catalog
`J/PASP/110/863` (<https://cdsarc.cds.unistra.fr/ftp/J/PASP/110/863/>).

## Processing

Each file here (`<type>.txt`, e.g. `g0v.txt`) comes from converting the
matching original `<type>.dat.gz` file in the source catalog:

1. The conversion parses the original fixed-width columns: wavelength in
   Angstroms in columns 1-7, and the component-averaged normalized flux
   (`nflam`) in columns 8-17. Hot stars have a flux value wider than the
   usual field width, so the columns cannot be split on whitespace.
2. The conversion trims each spectrum to 3000-10000 A, the wavelength range
   of the ZWO ASI 533MM Pro sensor (`sensor_min_wavelength` and
   `sensor_max_wavelength` in `astrometrics.config`), at the library's
   native 5 A sampling.
3. The conversion keeps only wavelength and flux. Classification does not
   use the source catalog's per-component columns or its
   standard-deviation column.

The 34 dwarf-star files were downloaded on 2026-09-19 (23-36 KB each, 1.1 MB
total) from the catalog directory listed under "Source". Reproducing them
requires downloading the same `<type>.dat.gz` files again and repeating the
three steps above.

For the coolest main-sequence types (M4V, M5V, M6V), the source library
reports a flux of zero at a few sample points between 3000 and 3885 A; it
has no real measurement below about 3900 A for these types. Classification
never compares wavelengths below 4200 A, so these zero values do not affect
classification results. The conversion keeps the zeros as the source
library reports them, rather than replacing them with an estimate.

These files use a plain comma-separated `.txt` extension rather than
`.csv`. The repository's `.gitattributes` routes `*.csv` files through Git
LFS, and continuous-integration checkouts do not fetch LFS content; keeping
these small bundled tables as plain blobs ensures CI can read them.

Flux values stay normalized to 1.0 at 5556 A, the source library's own
convention. `spectral_classifier.py` renormalizes both the reference
spectrum and the observed spectrum to a common scale before comparing them,
so this reference wavelength does not need to match the observed
spectrum's own calibration.

## Coverage

O5V, O9V, B0V, B1V, B3V, B8V, B9V, A0V, A2V, A3V, A5V, A7V, F0V, F2V, F5V,
F6V, F8V, G0V, G2V, G5V, G8V, K0V, K2V, K3V, K4V, K5V, K7V, M0V, M1V, M2V,
M3V, M4V, M5V, M6V — every single- or double-subtype rung the Pickles
library offers for solar-abundance dwarfs, from hottest to coolest.
Including every rung avoids the case where a star's true type falls between
two widely-spaced reference templates: with only every fifth subtype
sampled, such a star would sit roughly equidistant from its two nearest
templates, and the classifier would report the match as ambiguous (the
`is_classification_ambiguous` flag in the quality summary) more often than
necessary. With every rung present, a star between two adjacent types still
has one template that is clearly closer than the rest.

The full Pickles library also includes metal-weak and metal-rich variants
of several F-K dwarf types (e.g. `wg5v.dat`, `rk0v.dat`) and subgiants
(luminosity class IV). This directory does not bundle those. It also omits
two library types that duplicate the standard rungs above: `b57v.dat`, a
merged B5-7V spectrum, and `m2p5v.dat`, halfway between M2V and M3V. None
of these additions would improve this classifier's task of placing an
unknown star on the O-B-A-F-G-K-M sequence; including them would only add
reference correlations to compute for each star.

## Giant and supergiant references

Fifty-six standard-abundance spectra of luminosity classes I, II and III
from the same catalog. The download happened on 2026-09-24 (58 files of
about 30 KB each, 1.7 MB total, from
<https://cdsarc.cds.unistra.fr/ftp/J/PASP/110/863/>); the conversion
followed the same three steps described under "Processing" above.

These references let the classifier match a giant against a giant template
instead of against the dwarf template that happens to look most similar.
Without them, the classifier matches catalog type K3II (the star HD 183753)
and catalog type K1.5III (the star Arcturus) both to a K7V dwarf template.

Labels follow the library's own naming: `b12iii` is B1-2 III, `k01ii` is
K0-1 II, `k34ii` is K3-4 II. This directory omits two of the 58 downloaded
files, `m9iii` and `m10iii`, because their source data contains small
negative flux values near 4700-4800 A (down to -0.13). The well-formed-
template test used elsewhere in this pipeline rejects negative flux, and
the project's policy of keeping source data as given, described above,
rules out replacing those values with an estimate. No spectrum in this
project's star catalog has a type as late as M9 or M10, so this omission
does not currently limit classification.

Coverage across luminosity classes is uneven: 30 class III, 18 class I, and
8 class II spectra, against 34 dwarf spectra. Class II coverage in
particular has only a few rungs, so a bright giant's matched template can
be several subclasses away from its true type. `spectral_classifier.py`
lists these references in `GIANT_REFERENCE_SPECTRAL_TYPES`. The
main-sequence ladder, `REFERENCE_SPECTRAL_TYPES`, remains the basis for the
instrument response and for the catalog-type lookup used with dwarfs.

## Instrument response

`instrument_response_zwo_asi_533mm_pro.json` records the smooth tilt that
this instrument (the ZWO ASI 533MM Pro camera, grating, and telescope)
applies to every spectrum it captures. This file does not come from the
Pickles library; the project derives it from an observation. Correcting
for this tilt is necessary for the reference comparison to be meaningful:
without this correction, every reference spectrum correlates at 0.95 or
higher with every observed star, and the classifier matched a Vega spectrum
to type F6V instead of its correct type, A0V.

### Derivation

The `python -m astrometricslib.scripts.derive_instrument_response` script
builds this file:

1. The script extracts the brightest star from Vega's master spectral stack
   (spectral type A0V) and corrects it for the sensor's quantum
   efficiency.
2. The script divides that corrected spectrum by the bundled `a0v.txt`
   reference spectrum, after blurring the reference to the instrument's
   resolution (30 A).
3. The script fits the logarithm of that ratio with a degree-4 polynomial
   over the range 4200-8000 A. The fit skips a 60 A window around the
   strong Balmer and Ca H lines, so Vega's own absorption dips do not
   become part of the recorded instrument response.

The resulting JSON file stores the polynomial's coefficients (highest power
first), evaluated against `(wavelength - 6000) / 2000`; the wavelength
range over which the fit is valid; and a note identifying the observation
used. Dividing an observed spectrum by this response removes the
instrument's tilt.

### Validity

This response is specific to one instrument setup. Re-derive it after any
change to the camera, grating, or telescope. Validation against two stars
with an independently known catalog type confirms the response's
usefulness: the classifier matches Alcor's second star to type A5V
(catalog type A5V) and HD 151023 to type K2V (catalog type K0). Most other
spectra in this project's catalog do not yet match any reference closely;
`validate_spectral_and_period_analysis.py` reports the current numbers for
the full catalog.

### Which range is compared

The reference spectra cover the camera's entire sensitive range, but the
classifier compares an observed spectrum against them only within the
range where the instrument response is valid: 4200-8000 A by default.
Three effects make wavelengths above 8000 A less reliable: the sensor's
quantum efficiency falls from 27% to 6% in that range, which multiplies
noise by a factor of 4 to 16 when the pipeline corrects for it; water and
oxygen absorption bands from Earth's atmosphere appear; and blue light
begins arriving in second order. (A grism sends light of wavelength L to a
second position on the sensor that overlaps first-order light of
wavelength 2 x L. Because extraction starts at 3800 A, second-order light
from that wavelength reaches 7600 A and beyond.)

Extending the comparison range to 10000 A and re-fitting the response
changes the match quality unevenly. For three stars with relatively little
blue light — Vega (A0V), HD 151023 (K2V), and Alcor's second star (A7V) —
the match residual stays about the same (0.08, 0.11, and 0.08). For stars
with more blue light, such as HD 150679 (spectral type A2), the residual
rises substantially, from a range of 0.19-0.28 up to a range of 0.5-1.8.
Weighting each wavelength by the sensor's sensitivity does not correct
this: under that weighting, HD 150679 matches type F6V, which is
incorrect. Based on this result, 8000 A remains the default upper limit
for comparison.

A setup where extending the range would help, for example one that adds a
blocking filter to remove second-order light, can request the full range:

    python -m astrometricslib.scripts.derive_instrument_response --maximum-wavelength 10000
