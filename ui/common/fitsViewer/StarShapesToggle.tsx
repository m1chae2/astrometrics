/**
 * @file StarShapesToggle.tsx
 * @description The "Shapes" switch of the FITS viewer's header. While it is on, the viewer draws a short line
 * through every measured star along its long axis, as long as the star is stretched.
 */
import React from 'react';
import { Button } from '../components/Button';

/** The tooltip of the switch. */
export const STAR_SHAPES_TOGGLE_TOOLTIP =
  'Draw a short line on each measured star along its long axis, as long as the star is stretched. Lines that all point the same way mean tracking. Lines that point toward the center or one corner mean tilt or spacing. A star that is worse only in the corners means field curvature. Click a star to see it in detail.';

/** Props for {@link StarShapesToggle}. */
export interface StarShapesToggleProps {
  /** True while the overlay is on. */
  enabled: boolean;
  /** Called with the new setting when the user presses the button. */
  onChange: (enabled: boolean) => void;
  /** True to dim the control, for example while no image is open. */
  disabled?: boolean;
}

/**
 * Shows the Shapes switch for a viewer's header.
 *
 * @param props See {@link StarShapesToggleProps}.
 * @returns The button.
 */
export const StarShapesToggle: React.FC<StarShapesToggleProps> = ({ enabled, onChange, disabled = false }) => (
  <Button
    size="small"
    // Grey when off and blue when on, so it reads as a switch and not as another action button.
    variant={enabled ? 'default' : 'secondary'}
    pressed={enabled}
    disabled={disabled}
    title={STAR_SHAPES_TOGGLE_TOOLTIP}
    onClick={() => onChange(!enabled)}
  >
    Shapes
  </Button>
);
