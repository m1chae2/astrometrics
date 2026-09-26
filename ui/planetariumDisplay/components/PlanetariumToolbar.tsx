/**
 * @module PlanetariumToolbar
 * @fileoverview Floating overlay-toggle toolbar for the Planetarium viewport.
 *
 * Renders a row of labelled checkboxes for toggling sky map overlays, a Date and
 * Time button to open the simulation time modal, and a live FOV readout.
 *
 * REQ: PLN-2.1, REQ: PLN-2.2
 */

import React, { useState, useRef, useEffect } from 'react';

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
  /** Show telescope alignment pointing vectors and polar alignment overlay. */
  showAlignment: boolean;
  onToggleAlignment: (value: boolean) => void;
  /** Show mount tracking mechanical risk heatmap overlay. */
  showTrackingRisk?: boolean;
  onToggleTrackingRisk?: (value: boolean) => void;
  /** List of past observing sessions available for review. */
  availableSessions?: import('../../common/types/backendTypes').AlignmentSessionSummary[];
  /** Currently selected historical session identifier. */
  selectedSessionId?: string | null;
  /** Callback when user selects a different session. */
  onSelectSession?: (sessionId: string | null) => void;
  /** Callback to trigger syncing logs from the telescope. */
  onSyncLogs?: () => void;
  /** Whether a log synchronization is currently in progress. */
  isSyncingLogs?: boolean;
  /** Current field of view in degrees, displayed as a readout. */
  currentFOV: number;
  /** Opens the date/time simulation modal. */
  onOpenTimeModal: () => void;
  /** Opens the telescope performance diagnostics modal. */
  onOpenDiagnostics?: () => void;
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
  showAlignment,
  onToggleAlignment,
  showTrackingRisk = false,
  onToggleTrackingRisk,
  availableSessions = [],
  selectedSessionId = null,
  onSelectSession,
  onSyncLogs,
  isSyncingLogs = false,
  currentFOV,
  onOpenTimeModal,
  onOpenDiagnostics,
}) => {
  const [isLayersOpen, setIsLayersOpen] = useState(false);
  const layersRef = useRef<HTMLDivElement>(null);

  // Close layers popover on click outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (layersRef.current && !layersRef.current.contains(event.target as Node)) {
        setIsLayersOpen(false);
      }
    };
    if (isLayersOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isLayersOpen]);

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
    { label: 'FITS Solves', checked: showFITS, onChange: onToggleFITS },
    { label: 'Alignment', checked: showAlignment, onChange: onToggleAlignment },
  ];

  return (
    <div className="planetarium-toolbar">
      {/* Group 1: Passive Sky Layers Popover */}
      <div className="planetarium-toolbar__popover-container" ref={layersRef}>
        <button
          type="button"
          onClick={() => setIsLayersOpen((prev) => !prev)}
          className={`planetarium-toolbar__button ${isLayersOpen ? 'planetarium-toolbar__button--active' : ''}`}
          title="Toggle passive sky background and catalog layers"
        >
          <span>Layers</span>
          <span className="text-[11px] opacity-80">{isLayersOpen ? '▲' : '▼'}</span>
        </button>

        {isLayersOpen && (
          <div className="planetarium-toolbar__popover-menu animate-in fade-in zoom-in-95 duration-100">
            <div className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider px-1 pb-1 border-b border-white/10 mb-1">
              Sky Background
            </div>
            {passiveLayers.map(({ label, checked, onChange }) => (
              <label key={label} className="planetarium-toolbar__item cursor-pointer hover:text-sky-300 transition-colors">
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
      </div>

      <div className="planetarium-toolbar__divider" />

      {/* Group 2: Observation & Hardware Overlays */}
      <div className="planetarium-toolbar__group">
        {rigOverlays.map(({ label, checked, onChange }) => (
          <label key={label} className="planetarium-toolbar__item cursor-pointer">
            <input
              type="checkbox"
              checked={checked}
              onChange={(e) => onChange(e.target.checked)}
            />
            <span>{label}</span>
          </label>
        ))}
      </div>

      {/* Group 3: Session Selector & Log Sync (when Alignment or Tracking Heatmap is active) */}
      {(showAlignment || showTrackingRisk) && (
        <>
          <div className="planetarium-toolbar__divider" />
          <div className="planetarium-toolbar__session-container">
            <label htmlFor="planetarium-session-select" className="text-slate-400 text-[12px] font-medium">
              Session:
            </label>
            <select
              id="planetarium-session-select"
              value={selectedSessionId || ''}
              onChange={(e) => onSelectSession?.(e.target.value ? e.target.value : null)}
              className="planetarium-toolbar__select"
            >
              <option value="" className="bg-slate-900 text-slate-200">Live / Latest</option>
              {availableSessions.length > 0 && (
                <option value="all" className="bg-slate-900 text-slate-200">All Sessions (Cumulative)</option>
              )}
              {availableSessions.map((s) => {
                const paStr = s.polarErrorArcsec !== null && s.polarErrorArcsec !== undefined
                  ? ` • PA: ${(s.polarErrorArcsec / 60).toFixed(1)}'`
                  : '';
                const counts = [];
                if (s.targetCount) {
                  counts.push(`${s.targetCount} target${s.targetCount === 1 ? '' : 's'}`);
                }
                counts.push(`${s.syncCount} sync${s.syncCount === 1 ? '' : 's'}`);
                const countStr = counts.join(', ');

                return (
                  <option key={s.sessionId} value={s.sessionId} className="bg-slate-900 text-slate-200">
                    {s.sessionDate} ({countStr}{paStr})
                  </option>
                );
              })}
            </select>
            {onSyncLogs && (
              <button
                type="button"
                onClick={onSyncLogs}
                disabled={isSyncingLogs}
                title="Sync past logs and FITS solves from telescope"
                className="planetarium-toolbar__button !py-1 !px-2.5 !text-[12px]"
              >
                {isSyncingLogs ? 'Syncing...' : 'Sync'}
              </button>
            )}
          </div>
        </>
      )}

      <div className="planetarium-toolbar__divider" />

      {/* Group 4: Diagnostics, Time & Readouts */}
      <div className="planetarium-toolbar__group">
        {onOpenDiagnostics && (
          <button
            type="button"
            className="planetarium-toolbar__button"
            onClick={onOpenDiagnostics}
            title="Open Mount Pointing Model & Periodic Error Diagnostics"
          >
            Diagnostics
          </button>
        )}

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
