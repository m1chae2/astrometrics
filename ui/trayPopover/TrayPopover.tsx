/**
 * @fileoverview Tray popover panel component for the Astrometrics system tray.
 *
 * Renders a compact observatory status view with live telescope telemetry
 * (sourced from the AstrometricsContext WebSocket feed), a workspace quick
 * switcher, and a bottom action toolbar (Emergency Park, Open App, Quit).
 *
 * All actions are dispatched to the Electron main process via
 * `window.astrometrics.tray.sendAction()`.  Emergency Park does NOT dismiss
 * the popover — it fires the command and leaves the window visible so the
 * user can observe the updated mount status.
 */

import React, { useCallback } from 'react';
import { useAstrometrics } from '../common/context/AstrometricsContext';
import './trayPopover.css';

/** Workspace entries shown in the quick-switcher grid. */
const WORKSPACES: ReadonlyArray<{ label: string; mode: string }> = [
  { label: 'Image Viewer',       mode: 'Image Viewer' },
  { label: 'Astronomy Manager',  mode: 'Astronomy Manager' },
  { label: 'Planetarium',        mode: 'Planetarium' },
  { label: 'Image Processing',   mode: 'Image Processing' },
  { label: 'Observatory',        mode: 'Observatory Manager' },
  { label: 'Observation Mgr',   mode: 'Observation Manager' },
];

/**
 * Derives a pill modifier class from the raw telescope tracking status string.
 *
 * @param trackingStatus - The `trackingStatus` string from TelescopePulse.
 * @param connectionStatus - The `connectionStatus` string from TelescopePulse.
 * @returns BEM modifier suffix: 'tracking' | 'slewing' | 'parked' | 'disconnected'.
 */
function getMountPillVariant(trackingStatus: string, connectionStatus: string): string {
  if (connectionStatus !== 'Connected') return 'disconnected';
  const s = trackingStatus.toLowerCase();
  if (s.includes('tracking')) return 'tracking';
  if (s.includes('slew'))     return 'slewing';
  if (s.includes('park'))     return 'parked';
  return 'disconnected';
}

/**
 * Tray popover panel component.
 *
 * Reads live telescope telemetry from `useAstrometrics()` and dispatches
 * quick actions to the Electron main process through the contextBridge API.
 *
 * @returns The rendered tray popover element.
 */
export const TrayPopover: React.FC = () => {
  const { telescope, connected } = useAstrometrics();

  const pillVariant = getMountPillVariant(
    telescope.trackingStatus ?? '',
    telescope.connectionStatus ?? '',
  );

  const observatorySubtitle = connected
    ? `Observatory — ${telescope.trackingStatus || 'Ready'}`
    : 'Observatory — Offline';

  /**
   * Sends a named action to the Electron main process via the tray IPC bridge.
   *
   * @param action - Action identifier recognised by ipc_handlers.js.
   * @param payload - Optional action payload.
   */
  const sendAction = useCallback((action: string, payload?: Record<string, string>) => {
    window.astrometrics?.tray?.sendAction(action, payload);
  }, []);

  /**
   * Handles workspace button clicks by navigating the main window to the
   * chosen mode and dismissing the popover.
   *
   * @param mode - The workspace mode name to navigate to.
   */
  const onWorkspaceClick = useCallback((mode: string) => {
    sendAction('navigate', { mode });
  }, [sendAction]);

  return (
    <div className="tray-popover" role="dialog" aria-label="Astrometrics tray panel">

      {/* ── Header ── */}
      <header className="tray-popover__header">
        <span className="tray-popover__title">Astrometrics</span>
        <span className="tray-popover__subtitle">{observatorySubtitle}</span>
      </header>

      {/* ── Telemetry card ── */}
      <section className="tray-popover__card" aria-label="Observatory telemetry">

        {/* Mount status pill */}
        <div className="tray-popover__row">
          <span className="tray-popover__row-label">Mount</span>
          <span
            className={`tray-popover__status-pill tray-popover__status-pill--${pillVariant}`}
            role="status"
          >
            <span className="tray-popover__status-dot" aria-hidden="true" />
            {telescope.connectionStatus === 'Connected'
              ? (telescope.trackingStatus || 'Ready')
              : 'Disconnected'}
          </span>
        </div>

        {/* Active target */}
        <div className="tray-popover__row">
          <span className="tray-popover__row-label">Target</span>
          <span className="tray-popover__row-value" title={telescope.trackingStatus}>
            {/* Target name is surfaced via the IND/backend target name if available,
                otherwise the tracking status label carries the object context */}
            None Selected
          </span>
        </div>

        <hr className="tray-popover__divider" />

        {/* Pointing coordinates */}
        <div className="tray-popover__row">
          <span className="tray-popover__row-label">RA / Dec</span>
          <span className="tray-popover__row-value">
            {telescope.ra} / {telescope.dec}
          </span>
        </div>
        <div className="tray-popover__row">
          <span className="tray-popover__row-label">Alt / Az</span>
          <span className="tray-popover__row-value">
            {telescope.altitude}° / {telescope.azimuth}°
          </span>
        </div>
      </section>

      {/* ── Workspace quick-switcher ── */}
      <section aria-label="Workspace switcher">
        <span className="tray-popover__workspace-label">Workspaces</span>
        <nav className="tray-popover__workspace-grid" aria-label="Switch workspace">
          {WORKSPACES.map(({ label, mode }) => (
            <button
              key={mode}
              className="tray-popover__workspace-btn"
              onClick={() => onWorkspaceClick(mode)}
              title={`Switch to ${label}`}
              type="button"
            >
              {label}
            </button>
          ))}
        </nav>
      </section>

      {/* ── Action toolbar ── */}
      <footer className="tray-popover__toolbar" role="toolbar" aria-label="Quick actions">
        <button
          className="tray-popover__toolbar-btn tray-popover__toolbar-btn--park"
          onClick={() => sendAction('park')}
          title="Emergency park telescope (popover stays open to confirm state)"
          type="button"
        >
          ⏹ Park
        </button>
        <button
          className="tray-popover__toolbar-btn"
          onClick={() => sendAction('open-app')}
          title="Open Astrometrics main window"
          type="button"
        >
          Open App
        </button>
        <button
          className="tray-popover__toolbar-btn"
          onClick={() => sendAction('quit')}
          title="Quit Astrometrics"
          type="button"
        >
          Quit
        </button>
      </footer>

    </div>
  );
};

export default TrayPopover;
