/**
 * @file DetectionsToggle.tsx
 * @description The "Detections" switch of the FITS viewer's header. While it is on, the viewer marks every
 * star the astrometry run's star finder detects on the image shown.
 */
import React from 'react';
import { Button } from '../components/Button';

/** The tooltip of the switch. */
export const DETECTIONS_TOGGLE_TOOLTIP =
  'Mark every star the astrometry run detects on this image: circles for single stars, diamonds for stars reported twice and merged. Each circle is the circle photometry measures the star in. Use it to find a marker on nothing, a star with no marker, or a circle that misses its star.';

/** Props for {@link DetectionsToggle}. */
export interface DetectionsToggleProps {
  /** True while the overlay is on. */
  enabled: boolean;
  /** Called with the new setting when the user presses the button. */
  onChange: (enabled: boolean) => void;
  /** True to dim the control, for example while no image is open. */
  disabled?: boolean;
}

/**
 * Shows the Detections switch for a viewer's header.
 *
 * @param props See {@link DetectionsToggleProps}.
 * @returns The button.
 */
export const DetectionsToggle: React.FC<DetectionsToggleProps> = ({ enabled, onChange, disabled = false }) => (
  <Button
    size="small"
    // Grey when off and blue when on, so it reads as a switch and not as another action button.
    variant={enabled ? 'default' : 'secondary'}
    pressed={enabled}
    disabled={disabled}
    title={DETECTIONS_TOGGLE_TOOLTIP}
    onClick={() => onChange(!enabled)}
  >
    Detections
  </Button>
);
