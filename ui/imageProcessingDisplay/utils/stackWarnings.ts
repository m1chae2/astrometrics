/**
 * @file stackWarnings.ts
 * @description Plain words for the warnings of a stacking run. A warning is a stored check that
 * failed: it has a metric name, the number that was checked and the limit. This file gives each
 * stacking metric a short title and writes the number and the limit as text. A metric it does not
 * know gets a title made from its name, so no warning is ever hidden.
 */
import type { StackWarning } from '../../common/services/imaging/stackReviewService';

/** The titles of the stacking checks, by metric name. */
const STACK_WARNING_TITLES: Record<string, string> = {
    'stacking.gate_gain_homogeneity': 'Frames of different gain',
    'stacking.gate_frame_quarantine': 'Too many bad frames to set aside',
    'stacking.gate_background_homogeneity': 'Sky background split into groups',
    'stacking.gate_flat_level': 'Flat frames too faint or too bright',
    'stacking.gate_flat_noise': 'Noisy flat frame',
    'stacking.gate_calibration_metadata': 'Calibration frames do not match the lights',
    'stacking.gate_calibration_frame_count': 'Too few calibration frames',
    'stacking.gate_dark_level': 'Dark frame level is off',
    'stacking.gate_dark_noise': 'Dark frame noise is off',
    'stacking.gate_dark_zero_pixel_fraction': 'Dark frame has many zero pixels',
    'stacking.gate_dark_light_leak': 'Dark frame may have a light leak',
    'stacking.gate_bias_level': 'Bias frame level is off',
    'stacking.gate_bias_noise': 'Bias frame noise is off',
    'stacking.gate_bias_zero_pixel_fraction': 'Bias frame has many zero pixels',
    'stacking.gate_dark_missing': 'Dark frames missing',
    'stacking.gate_light_temperature_spread': 'Camera temperature drifted',
    'stacking.gate_exposure_group_linearity': 'Exposure group left out',
    'stacking.gate_exposure_groups_combined': 'Exposure group not combined',
    'stacking.gate_raw_frame_clipping': 'Raw frames cut off at zero',
    'stacking.gate_rejected_pixel_fraction': 'Many rejected pixels',
    'stacking.gate_stack_sharpness': 'Stars wider than expected',
    'stacking.gate_spectral_registration': 'Spectral frames may be misaligned',
    'stacking.gate_saturated_pixel_fraction': 'Many saturated pixels',
    'stacking.gate_zero_pixel_fraction': 'Many zero pixels in the stack',
    'stacking.gate_negative_pixels': 'Many negative pixels after calibration',
    'stacking.abandoned_after_s': 'Stacking ran out of time',
    'stacking.notice_frames_moved_to_excluded': 'Frames moved to the excluded folder',
    'stacking.notice_single_frame_stack': 'Stack of a single frame',
    'stacking.notice_exposure_group_left_out': 'Exposure group left out',
    'stacking.notice_flat_calibration_note': 'Flat frame problem',
};

/**
 * Gives a warning a short title.
 * @param metric The registered metric name.
 * @return The known title, or a title made from the last part of the name.
 */
export const describeStackWarningTitle = (metric: string): string => {
    const known = STACK_WARNING_TITLES[metric];
    if (known) return known;
    const lastPart = metric.split('.').pop() ?? metric;
    const words = lastPart.replace(/^gate_/, '').replace(/_/g, ' ').trim();
    return words ? words.charAt(0).toUpperCase() + words.slice(1) : metric;
};

/**
 * Writes one number of a warning with its unit.
 * @param number The number.
 * @param unit The unit; a `fraction` is written as a percent.
 * @return Text such as `16%`, `3.4 px` or `0.62`.
 */
export const formatWarningNumber = (number: number, unit: string): string => {
    if (unit === 'fraction') return `${Number((number * 100).toPrecision(3))}%`;
    const rounded = Number(number.toPrecision(3));
    return unit ? `${rounded} ${unit}` : `${rounded}`;
};

/**
 * Writes the number and the limit of a warning in one sentence.
 * @param warning The warning.
 * @return Text such as `Measured 16%; limit 15%.`, or an empty string when there is no number.
 */
export const describeWarningNumbers = (warning: StackWarning): string => {
    if (warning.value == null) return '';
    const measured = `Measured ${formatWarningNumber(warning.value, warning.unit)}`;
    return warning.limit == null
        ? `${measured}.`
        : `${measured}; limit ${formatWarningNumber(warning.limit, warning.unit)}.`;
};
