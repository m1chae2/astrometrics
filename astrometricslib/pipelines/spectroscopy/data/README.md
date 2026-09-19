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

1. Parsed the original fixed-width columns (wavelength in Angstroms in
   columns 1-7, the component-averaged normalized flux `nflam` in columns
   8-17; hot stars have a flux wider than the usual field, so the columns
   cannot be split on spaces).
2. Trimmed to 3000-10000 A, the wavelength range of the ZWO ASI 533MM Pro
   (`sensor_min_wavelength` and `sensor_max_wavelength` in
   `astrometrics.config`), at the library's own 5 A sampling. An earlier
   version of these files was trimmed to 3500-8000 A; over that range the
   values are identical to those files.
3. Kept only wavelength and flux; the original per-source-catalog component
   columns and standard-deviation column aren't needed for classification.

The files were downloaded on 2026-09-19 (34 files of 23-36 KB each, 1.1 MB in
all, from the catalog directory above). Re-deriving them means downloading
the same `<type>.dat.gz` files again and applying the steps above.

The library has no measurement below about 3900 A for the latest M types
(M4V, M5V and M6V have flux 0 at a few samples between 3000 and 3885 A).
Nothing is compared below 4200 A, so this does not affect classification,
but the zeros are kept as the source gives them rather than invented.

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

## Instrument response

`instrument_response_zwo_asi_533mm_pro.json` is the smooth tilt this
instrument (ZWO ASI 533MM Pro camera, grating and telescope) puts on every
spectrum. It is not from the Pickles library; it is derived from an
observation, and it is what makes matching a spectrum against the
references above meaningful (without it every reference correlates 0.95 or
better with every star, and a Vega spectrum matched F6V).

### Derivation

Made by `python -m astrometricslib.scripts.derive_instrument_response`:

1. The brightest star in Vega's master spectral stack (type A0V) is
   extracted and corrected for the sensor's quantum efficiency.
2. It is divided by the bundled `a0v.txt` reference, blurred to the
   instrument's resolution (30 A).
3. The logarithm of that ratio is fitted with a degree-4 polynomial over
   4200-8000 A, skipping 60 A around the strong Balmer and Ca H lines so
   the star's own dips are not part of the response.

The JSON stores the polynomial coefficients (highest power first) against
`(wavelength - 6000) / 2000`, the valid wavelength range, and a note on
the observation used. Dividing a spectrum by the response removes the
tilt.

### Validity

The response belongs to one setup. Re-derive it after any change to the
camera, grating or telescope. It was checked on Alcor's second star
(matched A5V, catalog A5V) and HD 151023 (matched K2V, catalog K0), but
most other stored spectra still match no reference closely; see
`validate_spectral_and_period_analysis.py` for the current numbers.

### Which range is compared

The references cover the camera's whole range, but a spectrum is compared
with them only where the instrument response is valid, 4200-8000 A by
default. Above 8000 A the sensor's quantum efficiency falls from 27% to 6%
(so correcting for it multiplies noise by 4 to 16), water and oxygen bands
appear, and light from the blue arrives in second order: a grism sends light
of wavelength L to a second position that overlaps first-order light of
2 x L, and the extraction starts at 3800 A, so second-order light from there
reaches 7600 A and beyond.

Checked on the stored spectra in September 2026: fitting the response and
comparing out to 10000 A left Vega (A0V, residual 0.08), HD 151023 (K2V,
0.11) and Alcor's second star (A7V, 0.08) about as well matched as before,
but pushed stars with much blue light, such as HD 150679 (A2), from
residuals of 0.19-0.28 to 0.5-1.8. Weighting each wavelength by the
sensor's sensitivity did not repair that and turned HD 150679 into a
confident wrong match (F6V). So 8000 A is the default upper limit.

The full range is supported for a setup where it might help (for example a
blocking filter that removes second-order light):

    python -m astrometricslib.scripts.derive_instrument_response --maximum-wavelength 10000
