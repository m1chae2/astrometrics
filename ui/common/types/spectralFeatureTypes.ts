/**
 * Types for the absorption-feature and emission-line tests on a spectrum.
 *
 * `SpectroscopyResult.probableSpectralFeatures` is a list of plain objects
 * on the Python side, so the code generator can only type it as
 * `Record<string, any>[]`. These are the shapes the feature detector
 * (`spectral_feature_detector.py`) actually produces, kept here by hand
 * because `backendTypes.ts` is regenerated and would drop them.
 */

/**
 * What the test for one named absorption feature concluded. "inconclusive" means a dip was
 * measured but this spectrum's noise is too large to call it real or rule it out.
 */
export type SpectralFeatureVerdict = 'detected' | 'possible' | 'inconclusive' | 'not_detected' | 'not_covered';

/** The result of testing one named absorption feature (such as H-alpha) in a spectrum. */
export interface SpectralFeatureResult {
  /** The feature's name. */
  feature: string;
  /** The feature's rest wavelength, in Angstroms. */
  wavelength_angstrom: number;
  /** What the test concluded. */
  verdict: SpectralFeatureVerdict;
  /**
   * Which way the feature points: a dip below the continuum ("absorption") or a bump above it
   * ("emission"). Missing for a feature the spectrum does not reach. H-alpha and H-beta are tested
   * both ways; every other feature only as absorption.
   */
  kind?: 'absorption' | 'emission';
  /** Where the best dip or bump was found, in Angstroms. Missing for a feature the spectrum does not reach. */
  measured_wavelength_angstrom?: number;
  /** How far below the continuum a dip is, or above it a bump is (see `kind`), as a fraction. */
  depth?: number;
  /** The one-sigma uncertainty of the depth. */
  depth_uncertainty?: number;
  /** Depth divided by its uncertainty. */
  significance?: number;
  /** The chance that noise like this spectrum's gives a dip or bump at least this significant, in either direction. */
  p_value?: number;
  /** The same chance for the reported direction alone, before allowing for the two directions tried. */
  p_value_one_sided?: number;
  /** How the p-value was found. */
  p_value_method?: string;
  /** The depth a star of the expected type should show, or null without a reference type. */
  expected_depth?: number | null;
  /** A model-based chance the line is present, or null without a reference type. */
  probability_present?: number | null;
  /**
   * The name of a nearby feature that keeps the same dip, or null. The two lines are closer together
   * than the instrument can separate, so the dip is credited to the one nearer its rest wavelength and
   * this feature's verdict is capped at "inconclusive".
   */
  blended_with?: string | null;
}

/**
 * What the test for one emission line (or blend of lines) concluded. "unclear" means a hump was
 * measured but is not far enough above the noise to call real.
 */
export type EmissionLineVerdict = 'detected' | 'unclear' | 'not_seen' | 'not_covered';

/**
 * The result of testing one emission line, or one blend of lines too close to tell apart, in a
 * glowing-gas spectrum. Produced by `emission_line_detector.py` and stored in
 * `SpectroscopyResult.emissionLines`.
 */
export interface EmissionLineResult {
  /** The display name; blends join their lines with " / ". */
  line: string;
  /** The named lines this entry stands for. */
  members: string[];
  /** The rest wavelengths of those lines, in Angstroms. */
  rest_wavelengths_angstrom: number[];
  /** Whether more than one named line is inside this entry. */
  is_blend: boolean;
  /** Where this entry's second-order copy would land, in Angstroms. It is not fitted. */
  second_order_ghost_angstrom: number;
  /** What the test concluded. */
  verdict: EmissionLineVerdict;
  /** Height above the continuum, in the spectrum's own units. Missing when not covered. */
  amplitude?: number;
  /** The one-sigma uncertainty of the amplitude. */
  amplitude_uncertainty?: number;
  /** Amplitude divided by its uncertainty. */
  significance?: number;
}
