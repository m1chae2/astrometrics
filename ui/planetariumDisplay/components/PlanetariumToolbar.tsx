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
import { Popover } from '../../common/components/Popover';

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
  /** Show mount tracking mechanical risk heatmap overlay. */
  showTrackingRisk?: boolean;
  onToggleTrackingRisk?: (value: boolean) => void;
  /** List of past observing sessions available for review. */
  availableSessions?: import('../../common/types/backendTypes').AlignmentSessionSummary[];
  /** Currently selected historical session identifier. */
  selectedSessionId?: string | null;
  /** Callback when user selects a different session. */
  onSelectSession?: (sessionId: string | null) => void;
  /** Current field of view in degrees, displayed as a readout. */
  currentFOV: number;
  /** Opens the date/time simulation modal. */
  onOpenTimeModal: () => void;
}

/**
 * Glassmorphic floating toolbar for toggling Planetarium overlay layers.
 *
 * Positioned absolutely at the top-right of the viewport container.
 * Groups passive celestial layers into a dropdown popover while keeping active
 * rig overlays and diagnostics immediately accessible.
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
  showTrackingRisk = false,
  onToggleTrackingRisk,
  availableSessions = [],
  selectedSessionId = null,
  onSelectSession,
  currentFOV,
  onOpenTimeModal,
}) => {
  // A native <select>'s open dropdown is drawn by the OS's own widget toolkit
  // on Linux (GTK), which follows the system theme rather than this page's
  // CSS `color-scheme: dark` — the popup keeps coming back light regardless
  // of what's declared here. Using our own Popover (like the Layers menu
  // below) keeps the session picker themed consistently everywhere.

  /**
   * Builds the display label for a historical session option, e.g.
   * "2026-09-24 (8 targets, 1 sync)".
   */
  const getSessionLabel = (s: NonNullable<Props['availableSessions']>[number]): string => {
    const paStr = s.polarErrorArcsec !== null && s.polarErrorArcsec !== undefined
      ? ` • PA: ${(s.polarErrorArcsec / 60).toFixed(1)}'`
      : '';
    const counts: string[] = [];
    if (s.targetCount) {
      counts.push(`${s.targetCount} target${s.targetCount === 1 ? '' : 's'}`);
    }
    counts.push(`${s.syncCount} sync${s.syncCount === 1 ? '' : 's'}`);
    return `${s.sessionDate} (${counts.join(', ')}${paStr})`;
  };

  const selectedSession = selectedSessionId
    ? availableSessions.find((s) => s.sessionId === selectedSessionId)
    : undefined;
  const selectedSessionLabel = selectedSessionId === null
    ? 'No Session Selected'
    : selectedSessionId === 'all'
      ? 'All Sessions (Cumulative)'
      : (selectedSession ? getSessionLabel(selectedSession) : 'No Session Selected');

  const passiveLayers = [
    { label: 'Stars', checked: showStars, onChange: onToggleStars },
    { label: 'Constellations', checked: showConstellations, onChange: onToggleConstellations },
    { label: 'Coordinate Grid', checked: showGrid, onChange: onToggleGrid },
    { label: 'Deep Catalog', checked: showCatalog, onChange: onToggleCatalog },
    { label: 'Ground Horizon', checked: showEnvironment, onChange: onToggleEnvironment },
    ...(onToggleTrackingRisk ? [{ label: 'Tracking Risk Heatmap', checked: showTrackingRisk, onChange: onToggleTrackingRisk }] : []),
  ];

  const rigOverlays = [
    { label: 'Telescope', checked: showTelescope, onChange: onToggleTelescope },
    { label: 'FOV Outline', checked: showFOV, onChange: onToggleFOV },
  ];

  return (
    <div className="planetarium-toolbar">
      {/* Group 1: Passive Sky Layers Popover */}
      <Popover
        className="planetarium-toolbar__popover-container"
        trigger={({ isOpen, toggle }) => (
          <button
            type="button"
            onClick={toggle}
            className={`planetarium-toolbar__button ${isOpen ? 'planetarium-toolbar__button--active' : ''}`}
            title="Toggle passive sky background and catalog layers"
          >
            <span>Layers</span>
            <span className="planetarium-toolbar__chevron">{isOpen ? '▲' : '▼'}</span>
          </button>
        )}
      >
        {() => (
          <div className="planetarium-toolbar__popover-menu">
            <div className="planetarium-toolbar__popover-header">
              Sky Background
            </div>
            {passiveLayers.map(({ label, checked, onChange }) => (
              <label key={label} className="planetarium-toolbar__item">
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={(e) => onChange(e.target.checked)}
                />
                <span>{label}</span>
              </label>
            ))}
          </div>
        )}
      </Popover>

      <div className="planetarium-toolbar__divider" />

      {/* Group 2: Observation & Hardware Overlays */}
      <div className="planetarium-toolbar__group">
        {rigOverlays.map(({ label, checked, onChange }) => (
          <label key={label} className="planetarium-toolbar__item">
            <input
              type="checkbox"
              checked={checked}
              onChange={(e) => onChange(e.target.checked)}
            />
            <span>{label}</span>
          </label>
        ))}
      </div>

      {/* Group 3: Session Selector — alignment is always active, so this is always shown. */}
      <div className="planetarium-toolbar__divider" />
      <div className="planetarium-toolbar__session-container">
        <Popover
          className="planetarium-toolbar__popover-container"
          trigger={({ isOpen, toggle }) => (
            <button
              type="button"
              onClick={toggle}
              className={`planetarium-toolbar__button planetarium-toolbar__select ${isOpen ? 'planetarium-toolbar__button--active' : ''}`}
            >
              <span>{selectedSessionLabel}</span>
              <span className="planetarium-toolbar__chevron">{isOpen ? '▲' : '▼'}</span>
            </button>
          )}
        >
          {({ close }) => (
            <div className="planetarium-toolbar__popover-menu planetarium-toolbar__popover-menu--sessions">
              <button
                type="button"
                className={`planetarium-toolbar__session-option ${selectedSessionId === null ? 'planetarium-toolbar__session-option--selected' : ''}`}
                onClick={() => { onSelectSession?.(null); close(); }}
              >
                No Session Selected
              </button>
              {availableSessions.length > 0 && (
                <button
                  type="button"
                  className={`planetarium-toolbar__session-option ${selectedSessionId === 'all' ? 'planetarium-toolbar__session-option--selected' : ''}`}
                  onClick={() => { onSelectSession?.('all'); close(); }}
                >
                  All Sessions (Cumulative)
                </button>
              )}
              {availableSessions.map((s) => (
                <button
                  type="button"
                  key={s.sessionId}
                  className={`planetarium-toolbar__session-option ${selectedSessionId === s.sessionId ? 'planetarium-toolbar__session-option--selected' : ''}`}
                  onClick={() => { onSelectSession?.(s.sessionId); close(); }}
                >
                  {getSessionLabel(s)}
                </button>
              ))}
            </div>
          )}
        </Popover>
      </div>

      <div className="planetarium-toolbar__divider" />

      {/* Group 4: Time & Readouts */}
      <div className="planetarium-toolbar__group">
        <button
          type="button"
          className="planetarium-toolbar__button"
          onClick={onOpenTimeModal}
          title="Configure simulation date and time"
        >
          Date &amp; Time
        </button>

        <div className="planetarium-toolbar__readout">
          FOV {currentFOV.toFixed(1)}&deg;
        </div>
      </div>
    </div>
  );
};
