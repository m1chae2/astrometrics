/**
 * @module PlanetariumToolbar
 * @fileoverview Floating overlay-toggle toolbar for the Planetarium viewport.
 *
 * Renders a row of labelled checkboxes for toggling sky map overlays, a Date and
 * Time button to open the simulation time modal, and a live FOV readout.
 *
 * REQ: PLN-2.1, REQ: PLN-2.2
 */

import React from 'react';

/**
 * Props for PlanetariumToolbar.
 *
 * Each overlay has a paired boolean state and setter. The naming convention is
 * `show<Layer>` / `onToggle<Layer>`.
 */
interface Props {
  /** Show star catalog overlay. */
  showStars: boolean;
  onToggleStars: (value: boolean) => void;
  /** Show sensor FOV outline. */
  showFOV: boolean;
  onToggleFOV: (value: boolean) => void;
  /** Show FITS image overlays. */
  showFITS: boolean;
  onToggleFITS: (value: boolean) => void;
  /** Show local horizon and ground shading. */
  showEnvironment: boolean;
  onToggleEnvironment: (value: boolean) => void;
  /** Show RA/Dec coordinate grid. */
  showGrid: boolean;
  onToggleGrid: (value: boolean) => void;
  /** Show cataloged (library) objects. */
  showCatalog: boolean;
  onToggleCatalog: (value: boolean) => void;
  /** Show bundled constellation stick-figure lines. */
  showConstellations: boolean;
  onToggleConstellations: (value: boolean) => void;
  /** Show telescope pointing crosshair. */
  showTelescope: boolean;
  onToggleTelescope: (value: boolean) => void;
  /** Current field of view in degrees, displayed as a readout. */
  currentFOV: number;
  /** Opens the date/time simulation modal. */
  onOpenTimeModal: () => void;
}

/**
 * Descriptor for a single overlay toggle checkbox rendered in the toolbar.
 */
interface OverlayToggle {
  /** Human-readable label shown next to the checkbox. */
  label: string;
  /** Current checked state. */
  checked: boolean;
  /** Callback invoked with the new boolean value when the checkbox changes. */
  onChange: (value: boolean) => void;
}

/**
 * Glassmorphic floating toolbar for toggling Planetarium overlay layers.
 *
 * Positioned absolutely at the top-right of the viewport container.
 * Each checkbox directly controls an overlay visibility flag in the parent.
 *
 * @func PlanetariumToolbar
 * @param {Props} props - Component props.
 * @returns {React.ReactElement} The rendered toolbar.
 */
export const PlanetariumToolbar: React.FC<Props> = ({
  showStars,
  onToggleStars,
  showFOV,
  onToggleFOV,
  showFITS,
  onToggleFITS,
  showEnvironment,
  onToggleEnvironment,
  showGrid,
  onToggleGrid,
  showCatalog,
  onToggleCatalog,
  showConstellations,
  onToggleConstellations,
  showTelescope,
  onToggleTelescope,
  currentFOV,
  onOpenTimeModal
}) => {
  const overlayToggleList: OverlayToggle[] = [
    { label: 'Stars',        checked: showStars,       onChange: onToggleStars },
    { label: 'Grid',         checked: showGrid,         onChange: onToggleGrid },
    { label: 'Environment',  checked: showEnvironment, onChange: onToggleEnvironment },
    { label: 'FOV Outline',  checked: showFOV,         onChange: onToggleFOV },
    { label: 'FITS Overlays',checked: showFITS,        onChange: onToggleFITS },
    { label: 'Cataloged',    checked: showCatalog,     onChange: onToggleCatalog },
    { label: 'Constellations', checked: showConstellations, onChange: onToggleConstellations },
    { label: 'Telescope',    checked: showTelescope,   onChange: onToggleTelescope },
  ];

  return (
    <div className="planetarium-toolbar">
      {overlayToggleList.map(({ label, checked, onChange }) => (
        <label key={label} className="planetarium-toolbar__item cursor-pointer">
          <input
            type="checkbox"
            checked={checked}
            onChange={(e) => onChange(e.target.checked)}
          />
          <span>{label}</span>
        </label>
      ))}

      {/* Date & Time Button */}
      <button
        className="planetarium-toolbar__button"
        onClick={onOpenTimeModal}
      >
        Date &amp; Time
      </button>

      {/* FOV Readout */}
      <div className="planetarium-toolbar__readout">
        FOV {currentFOV.toFixed(1)}&deg;
      </div>
    </div>
  );
};
