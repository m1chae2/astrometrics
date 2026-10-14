/**
 * @module constants
 * @fileoverview Shared pixel/angle tuning knobs used across the canvas hit-testing
 * and overlay layers, gathered in one place so they can be tuned consistently
 * instead of being grepped for across a dozen files. Each constant's rationale
 * lives in its own comment below rather than scattered at each call site.
 */

/** Hit-test radius (px) for a plate-solve alignment sync point on the canvas. */
export const ALIGNMENT_HIT_RADIUS_PX = 24;

/** Hit-test radius (px) for a star source on the canvas. */
export const STAR_HIT_RADIUS_PX = 18;

/** Extra hit-box margin (px) added around a target's dynamic reticle radius. */
export const TARGET_HIT_MARGIN_PX = 8;

/** Alt-axis sampling step (deg) for the TrackingRiskOverlay heatmap dome. */
export const TRACKING_RISK_ALT_STEP_DEG = 10;

/** Az-axis sampling step (deg) for the TrackingRiskOverlay heatmap dome. */
export const TRACKING_RISK_AZ_STEP_DEG = 15;

/** Off-screen margin (px) beyond which a TrackingRiskOverlay heatmap quad is skipped entirely. */
export const TRACKING_RISK_OFFSCREEN_MARGIN_PX = 80;

/** Radius (px) of the plate-solve sync point reticle drawn by AlignmentOverlay. */
export const ALIGNMENT_RETICLE_RADIUS_PX = 8;

/** Arrowhead tip size (px) for AlignmentOverlay's pointing-error vectors. */
export const ALIGNMENT_ARROW_HEAD_PX = 6;
