# Spectroscopy data files

This folder holds the data files the spectroscopy pipeline reads at run time.

Terms are defined in the [glossary](../../../../documentation/Glossary.md). This document also defines each term the first time it appears.

Wavelengths are in ångströms (Å; 1 Å is 0.1 nm). A spectral type such as G0V names the star's place on the O-B-A-F-G-K-M temperature ladder (hottest to coolest, each letter split into subtypes 0 to 9) and its luminosity class (V for a dwarf or main-sequence star, III for a giant, II for a bright giant, I for a supergiant).

The folder holds these files:

- Ninety reference spectra (`<type>.txt`) that `spectral_classifier.py` uses to estimate a star's broad spectral type by comparing an observed spectrum's shape against these known ones: 34 main-sequence stars and 56 giants, bright giants and supergiants (see [Giant and supergiant references](#giant-and-supergiant-references) below). The sections up to [Instrument response](#instrument-response) describe them.
- `instrument_response_zwo_asi_533mm_pro.json`, the instrument's measured tilt: how much more or less light the camera, grating and telescope pass at each wavelength (see [Instrument response](#instrument-response)).
- `line_spread_zwo_asi_533mm_pro.json`, the instrument's blur at each wavelength (see [Line spread](#line-spread)).
- `cross_trail_blur_zwo_asi_533mm_pro.json`, the shape of a spectrum trail (a star's spread-out spectrum on the image) across the dispersion direction, the direction in which the grating spreads the light (see [Cross-trail blur](#cross-trail-blur)).
- `atmospheric_extinction_kpno.txt`, the air's dimming at each wavelength (see [Atmospheric extinction](#atmospheric-extinction)).

The camera-specific JSON files are found by their `camera_name` field, so a second camera needs its own file of each kind.

## Source

Pickles, A.J. 1998, "A Stellar Spectral Flux Library: 1150 to 25000 Å", *Publications of the Astronomical Society of the Pacific*, 110, 863 (1998PASP..110..863P). Retrieved from the CDS VizieR archive, catalog `J/PASP/110/863` (<https://cdsarc.cds.unistra.fr/ftp/J/PASP/110/863/>).

## Processing

Each file here (`<type>.txt`, e.g. `g0v.txt`) comes from converting the matching original `<type>.dat.gz` file in the source catalog:

1. The conversion parses the original fixed-width columns: wavelength in ångströms in columns 1 to 7, and the component-averaged normalized flux (`nflam`) in columns 8 to 17. The `nflam` value is brightness per unit wavelength scaled to 1 at a reference wavelength. Pickles built each template from several published spectra, its components, and this column is their average. Hot stars have a flux value wider than the usual field width, so the columns cannot be split on whitespace.
2. The conversion trims each spectrum to 3000 to 10,000 Å, the wavelength range of the ZWO ASI 533MM Pro sensor (`sensor_min_wavelength` and `sensor_max_wavelength` in `astrometrics.config.toml`), at the library's native 5 Å sampling.
3. The conversion keeps only wavelength and flux. Classification does not use the source catalog's per-component columns or its standard-deviation column.

The 34 dwarf-star files were downloaded on 2026-09-19 (23 to 36 KB each, 1.1 MB total) from the catalog directory listed under [Source](#source). Reproducing them requires downloading the same `<type>.dat.gz` files again and repeating the three steps above.

For the coolest main-sequence types (M4V, M5V, M6V), the source library reports a flux of zero at a few sample points between 3000 and 3885 Å; it has no real measurement below about 3900 Å for these types. Classification never compares wavelengths below 4200 Å, so these zero values do not affect classification results. The conversion keeps the zeros as the source library reports them, rather than replacing them with an estimate.

These files use a plain comma-separated `.txt` extension rather than `.csv`. The repository's `.gitattributes` routes `*.csv` files through Git LFS (Large File Storage, which keeps big files outside the normal history), and continuous-integration checkouts do not fetch LFS content; keeping these small bundled tables as plain blobs ensures CI can read them.

Flux values stay normalized to 1.0 at 5556 Å, the source library's own convention. `spectral_classifier.py` renormalizes both the reference spectrum and the observed spectrum to a common scale before comparing them, so this reference wavelength does not need to match the observed spectrum's own calibration.

## Coverage

O5V, O9V, B0V, B1V, B3V, B8V, B9V, A0V, A2V, A3V, A5V, A7V, F0V, F2V, F5V, F6V, F8V, G0V, G2V, G5V, G8V, K0V, K2V, K3V, K4V, K5V, K7V, M0V, M1V, M2V, M3V, M4V, M5V, M6V — every single- or double-subtype rung the Pickles library offers for solar-abundance dwarfs, from hottest to coolest. Including every rung avoids the case where a star's true type falls between two widely-spaced reference templates. With only every fifth subtype sampled, such a star would sit roughly equidistant from its two nearest templates (reference spectra). The classifier would then report the match as ambiguous (the `is_classification_ambiguous` flag in the quality summary) more often than necessary. With every rung present, a star between two adjacent types still has one template that is clearly closer than the rest.

The full Pickles library also includes metal-weak and metal-rich variants (stars with less or more of the elements heavier than helium than the Sun) of several F-K dwarf types (e.g. `wg5v.dat`, `rk0v.dat`) and subgiants (luminosity class IV). This directory does not bundle those. It also omits two library types that duplicate the standard rungs above: `b57v.dat`, a merged B5-7V spectrum, and `m2p5v.dat`, halfway between M2V and M3V. None of these additions would improve this classifier's task of placing an unknown star on the O-B-A-F-G-K-M sequence; including them would only add more reference comparisons to compute for each star.

## Giant and supergiant references

Fifty-six standard-abundance spectra of luminosity classes I, II and III from the same catalog. The download happened on 2026-09-24 (58 files of about 30 KB each, 1.7 MB total, from <https://cdsarc.cds.unistra.fr/ftp/J/PASP/110/863/>); the conversion followed the same three steps described under [Processing](#processing) above.

These references let the classifier match a giant against a giant template instead of against the dwarf template that happens to look most similar. Without them, a catalog giant matches a cool dwarf template instead (partly measured; see [SPE-20](../../../../documentation/library_design/validation/spectroscopy.md#spe-20)).

Labels follow the library's own naming: `b12iii` is B1-2 III, `k01ii` is K0-1 II, `k34ii` is K3-4 II. This directory omits two of the 58 downloaded files, `m9iii` and `m10iii`, because their source data contains small negative flux values near 4700 to 4800 Å (down to -0.13). The well-formed template test used elsewhere in this pipeline rejects negative flux, and the project's policy of keeping source data as given, described above, rules out replacing those values with an estimate. No spectrum in this project's star catalog has a type as late as M9 or M10, so this omission does not currently limit classification.

Coverage across luminosity classes is uneven: 30 class III, 18 class I, and 8 class II spectra, against 34 dwarf spectra. Class II coverage in particular has only a few rungs, so a bright giant's matched template can be several subclasses away from its true type. `spectral_classifier.py` lists these references in `GIANT_REFERENCE_SPECTRAL_TYPES`. The main-sequence ladder, `REFERENCE_SPECTRAL_TYPES`, remains the basis for the instrument response and for the catalog-type lookup used with dwarfs.

## Instrument response

`instrument_response_zwo_asi_533mm_pro.json` records the smooth tilt that this instrument (the ZWO ASI 533MM Pro camera, grating, and telescope) applies to every spectrum it captures. This file does not come from the Pickles library; the project derives it from an observation.

**Why it is needed.** Correcting for this tilt is necessary for the reference comparison to be meaningful. Without this correction, every reference spectrum looks alike to every observed star, and the classifier gives a wrong type even for Vega (partly measured; see [SPE-13](../../../../documentation/library_design/validation/spectroscopy.md#spe-13)).

### Derivation

The `python -m astrometricslib.scripts.derive_instrument_response` script builds this file:

1. The script extracts the brightest star from Vega's master spectral stack (spectral type A0V) and corrects it for the sensor's quantum efficiency (the fraction of arriving light the sensor records at each wavelength).
2. The script divides that corrected spectrum by the bundled `a0v.txt` reference spectrum, after blurring the reference to the instrument's resolution (its blur). The blur follows `line_spread_zwo_asi_533mm_pro.json`, which grows from 42 Å at 4200 Å to 148 Å at H-alpha. A single width measured from the trail is used only for a camera with no line-spread file.
3. The script fits the logarithm of that ratio with a degree-4 polynomial over the range 4200 to 8000 Å. The fit skips a band around each strong Balmer (hydrogen) and Ca H (ionized calcium) line, so Vega's own absorption dips do not become part of the recorded instrument response. Each band reaches 1.5 times the instrument's line spread (a full width at half maximum, in ångströms) at that line's wavelength, and at least 60 Å, to each side of the line. The line spread comes from `line_spread_zwo_asi_533mm_pro.json`, the file the classifier also reads. It is 148 Å at H-alpha, so the band there reaches 222 Å to each side. At H-gamma the line spread is 44 Å and the 60 Å minimum applies.

The resulting JSON file stores the polynomial's coefficients (highest power first), evaluated against `(wavelength - 6000) / 2000`; the wavelength range over which the fit is valid; a note identifying the observation used; and `reference_airmass`, the airmass of that observation (unitless; how much air the light crossed, 1.0 straight overhead). Dividing an observed spectrum by this response removes the instrument's tilt, along with the air's dimming at the reference airmass.

**The stored values.** The stored file records `reference_airmass` 1.15. That value is the airmass that `cross_trail_blur_zwo_asi_533mm_pro.json` logs for the 40 Vega frames of 0.25 s taken on 2026-09-25. The response's own `source` note describes the same set of frames.

**Known error in the stored coefficients.** The shipped file still holds these coefficients (its `source` note dates them 2026-09-25); re-deriving replaces them. They came from a fit that blurred the reference to one width and skipped a fixed 60 Å band around every line, so they carry some of Vega's line wings. The size of that error depends on the single width used, and the wavelength-dependent blur removes it (partly measured; see [SPE-13](../../../../documentation/library_design/validation/spectroscopy.md#spe-13)).

**Re-deriving.** Re-running the derivation script on Vega's stack replaces the coefficients. The script writes `reference_airmass` into the JSON. It reads the value from the stack's `AIRMASS` header card, or from the `--reference-airmass` option when the stack has no such card. With neither, it stores `null`, and the pipeline skips the airmass correction (see `pre_processing/README.md`). The script refuses a value outside 1 to 10.

### Validity

This response is specific to one instrument setup. Re-derive it after any change to the camera, grating, or telescope. Two stars with an independently known catalog type check the response (partly measured; see [SPE-13](../../../../documentation/library_design/validation/spectroscopy.md#spe-13)). `validate_spectral_and_period_analysis.py` reports the current numbers for the full catalog.

### Which range is compared

The reference spectra cover the camera's entire sensitive range, but the classifier compares an observed spectrum against them only within the range where the instrument response is valid: 4200 to 8000 Å by default. Three effects make wavelengths above 8000 Å less reliable:

- the sensor's quantum efficiency falls from 27% to 6% in that range, which multiplies noise by a factor of 4 to 16 when the pipeline corrects for it;
- water and oxygen absorption bands from Earth's atmosphere appear;
- blue light begins arriving in second order. (The grating sends light of wavelength L to a second position on the sensor that overlaps first-order light of wavelength 2 x L. Because extraction starts at 3800 Å, second-order light from that wavelength reaches 7600 Å and beyond.)

**Evidence.** Extending the comparison range to 10,000 Å and re-fitting the response leaves some stars about the same but makes the match residual (the relative RMS difference from the best reference; 0 is perfect) much worse for stars bright in the blue. Weighting each wavelength by the sensor's sensitivity does not correct this (partly measured; see [SPE-14](../../../../documentation/library_design/validation/spectroscopy.md#spe-14)). 8000 Å therefore remains the default upper limit for comparison.

A setup where extending the range would help, for example one that adds a blocking filter to remove second-order light, can request the full range:

    python -m astrometricslib.scripts.derive_instrument_response --maximum-wavelength 10000

## Line spread

`line_spread_zwo_asi_533mm_pro.json` records the resolution element (the full width at half maximum, FWHM, of the instrument's blur along the spectrum, in ångströms; the FWHM is the width where the blur profile drops to half its peak) at a few wavelengths: `wavelength_angstrom` and `resolution_element_angstrom`. The blur grows from 42 Å at 4200 Å to 148 Å at H-alpha (6563 Å), and the file holds that value out to 9000 Å (partly measured; see [SPE-06](../../../../documentation/library_design/validation/spectroscopy.md#spe-06)). The `source` field says how it was measured: the Gaussian blur that makes each catalog-type reference match the observed line depths of three stars, with the trail width used below 4400 Å. It describes one focus setup.

`load_line_spread_profile` in `pre_processing/spectral_resolution.py` reads it. The classifier blurs its references with it, and the wavelength zero-point fit, the equivalent widths, the measured-line-spread comparison, the Gaia XP comparison and `derive_instrument_response.py` use the same profile. For a camera with no file, they use one width for the whole spectrum: the resolution element measured from the trail, or a fixed fallback.

## Cross-trail blur

`cross_trail_blur_zwo_asi_533mm_pro.json` records the shape of an isolated bright star's trail across the dispersion direction. It comes from 40 short Vega frames, aligned on the zero order (the star's undispersed, point-like image) and averaged (the `source` field gives the date, the airmass and the focuser state). It holds `offsets_px`, the distance from the trail's center in pixels (-75 to +75), `band_edges_px`, eight bands along the trail from 340 to 680 pixels beyond the zero order (about 3800 to 7700 Å), and one normalized profile per band in `profiles`.

`load_cross_trail_blur` in `pre_processing/neighbor_wing_correction.py` reads it. The neighbor-wing correction fits this stored shape to a crowded star's trail to work out how much of a bright neighbor's light falls in its box. The instrument response section above also takes its reference airmass from this file's log.

## Atmospheric extinction

`atmospheric_extinction_kpno.txt` lists how much the air dims starlight at each wavelength. The columns are `wavelength_angstrom` (ångströms) and `extinction_mag_per_airmass` (magnitudes per airmass; one magnitude is a factor of 2.512 in brightness). Extinction is the dimming of starlight by the air. The file has 78 rows from 3200 Å to 8370 Å. The extinction coefficient falls from 1.017 at 3200 Å to 0.31 at 4190 Å, 0.148 at 5556 Å and 0.052 at 8000 Å.

`atmospheric_extinction.py` interpolates this table and uses it to scale a spectrum from one airmass to another (see the pre-processing README).

### Source

Mean extinction curve for Kitt Peak National Observatory, from the file `noao/lib/onedstds/kpnoextinct.dat` of the IRAF distribution (a long-standing astronomy data-reduction package) (<https://github.com/iraf-community/iraf>). The IRAF `onedstds` README describes it as the KPNO extinction table for ONEDSPEC. It does not give a journal reference for the KPNO values. The file here keeps rows up to 8370 Å unchanged from the source; the source's last four rows (8708 to 10,400 Å) are CTIO values that the IRAF file itself marks as not measured at KPNO, and this file leaves them out. The download happened on 2026-10-10.

Kitt Peak is a dry site at 2 km altitude. A site at lower altitude has somewhat larger extinction in the blue. The correction uses the difference between two airmasses, so the curve's shape matters more than its overall level.

For exact behavior, read the code.
