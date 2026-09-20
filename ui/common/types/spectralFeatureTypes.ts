/**
 * Types for the absorption-feature tests on a spectrum.
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
  /** Where the best dip was found, in Angstroms. Missing for a feature the spectrum does not reach. */
  measured_wavelength_angstrom?: number;
  /** How far below the continuum the dip is, as a fraction. */
  depth?: number;
  /** The one-sigma uncertainty of the depth. */
  depth_uncertainty?: number;
  /** Depth divided by its uncertainty. */
  significance?: number;
  /** The chance that noise like this spectrum's gives a dip at least this significant. */
  p_value?: number;
  /** How the p-value was found. */
  p_value_method?: string;
  /** The depth a star of the expected type should show, or null without a reference type. */
  expected_depth?: number | null;
  /** A model-based chance the line is present, or null without a reference type. */
  probability_present?: number | null;
}
