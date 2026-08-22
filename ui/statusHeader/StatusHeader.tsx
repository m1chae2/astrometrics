import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useStatusData } from './hooks/useStatusData';
import { useSettingsModal } from './hooks/useSettingsModal';
import { SettingsPanel } from './SettingsPanel';
import { useAstrometrics } from '../common/context/AstrometricsContext';
import './statusHeader.css';

/**
 * Component representing the application's top status header and settings menu.
 */
export const StatusHeader: React.FC = () => {
  // Hooks
  const {
    telemetry, connected, trackingStatus, telescopeConnection,
    selectedMode, chooseMode
  } = useStatusData();

  const { config } = useAstrometrics();

  const { open: settingsOpen, closing: settingsClosing, handleOpen: handleOpenSettings, handleClose: handleCloseSettings } = useSettingsModal();
  const { altitude, azimuth, temperature, humidity, ra, dec } = telemetry;

  // Mode Dropdown State
  const [modeOpen, setModeOpen] = useState(false);
  const [menuPosition, setMenuPosition] = useState<{ top: number; left: number; minWidth?: number } | null>(null);
  const modeButtonRef = useRef<HTMLButtonElement | null>(null);
  const modeMenuRef = useRef<HTMLDivElement | null>(null);

  // Calculate menu position when dropdown opens
  useEffect(() => {
    if (modeOpen && modeButtonRef.current) {
      const rect = modeButtonRef.current.getBoundingClientRect();
      setMenuPosition({
        top: rect.bottom + window.scrollY,
        left: rect.left + window.scrollX,
        minWidth: rect.width || undefined,
      });
    }
  }, [modeOpen]);

  // Mode Dropdown Logic (same as before)
  useEffect(() => {
    if (!modeOpen) return;
    const onDocClick = (e: MouseEvent): void => {
      const target = e.target as Node | null;
      if (!target) return;
      if (modeMenuRef.current?.contains(target)) return;
      if (modeButtonRef.current?.contains(target)) return;
      setModeOpen(false);
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [modeOpen]);

  const handleModeSelect = (mode: string) => {
    chooseMode(mode);
    setModeOpen(false);
  };

  // Render Helpers
  const renderConnectionStatus = () => {
    const modifier = connected === null ? 'unknown' : (connected ? 'connected' : 'disconnected');
    const txt = connected === null ? 'Checking…' : (connected ? 'Connected' : 'Disconnected');
    return <span className={`status-widget__value status-widget__value--${modifier}`}>{txt}</span>;
  };

  const renderTrackingStatus = () => {
    const val = trackingStatus || 'Not Tracking';
    const notTrackingRegex = /park|not track|not-tracking|not tracking/i;
    const modifier = notTrackingRegex.test(String(val)) ? 'not-tracking' : (telescopeConnection ? 'connected' : 'disconnected');
    return <span className={`status-widget__value status-widget__value--${modifier}`}>{val}</span>;
  };

  const availableModes = [
    'Image Viewer',
    'Image Processing',
    ...(config['Frontend']?.['enable_astronomy'] === 'true' ? ['Astronomy Manager'] : []),
    ...(config['Frontend']?.['enable_planetarium'] === 'true' ? ['Planetarium'] : []),
    ...(config['Frontend']?.['enable_observatory'] === 'true' ? ['Observatory Manager'] : []),
    ...(config['Frontend']?.['enable_observation'] === 'true' ? ['Observation Manager'] : []),
  ];

  return (
    <>
      <div className="header" role="status" aria-label="Application status">
        <div className="header__widgets">
          <div className="header__left">
            <div className="header__settings">
              <button
                className="header__settings-button"
                aria-label="Open settings"
                onClick={handleOpenSettings}
                type="button"
              >
                <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                  <rect x="3" y="6" width="18" height="2" rx="1" />
                  <rect x="3" y="11" width="18" height="2" rx="1" />
                  <rect x="3" y="16" width="18" height="2" rx="1" />
                </svg>
              </button>
              <div className="mode-selector">
                <button
                  ref={modeButtonRef}
                  className={`mode-selector__button ${modeOpen ? 'mode-selector__button--active' : ''}`}
                  onClick={() => setModeOpen((v) => !v)}
                  type="button"
                >
                  {selectedMode}
                </button>
                {modeOpen && menuPosition && createPortal(
                  <div
                    ref={modeMenuRef}
                    className="mode-selector__menu"
                    style={menuPosition}
                  >
                    {availableModes.map(m => (
                      <button
                        key={m}
                        className={`mode-selector__item ${selectedMode === m ? 'mode-selector__item--selected' : ''}`}
                        onClick={() => handleModeSelect(m)}
                        type="button"
                      >
                        {m}
                      </button>
                    ))}
                  </div>,
                  document.body
                )}
              </div>
            </div>
          </div>

          <div className="header__right">
            <div className="status-widget">
              <span className="status-widget__label">Connection Status</span>
              {renderConnectionStatus()}
            </div>

            <div className="status-widget">
              <span className="status-widget__label">Tracking Status</span>
              {renderTrackingStatus()}
            </div>

            <div className="status-widget">
              <span className="status-widget__label">Current Temperature</span>
              <span className="status-widget__value">
                <svg className="status-widget__icon" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M12 2a3 3 0 0 0-3 3v7a5 5 0 1 0 6 0V5a3 3 0 0 0-3-3z" />
                </svg>
                {temperature} <span className="status-widget__unit">°C</span>
              </span>
            </div>

            <div className="status-widget">
              <span className="status-widget__label">Current Humidity</span>
              <span className="status-widget__value">
                <svg className="status-widget__icon" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z" />
                </svg>
                {humidity} <span className="status-widget__unit">%</span>
              </span>
            </div>

            <div className="status-widget status-widget--large hide-mobile">
              <span className="status-widget__label">Altitude</span>
              <span className="status-widget__value">{altitude}</span>
            </div>

            <div className="status-widget status-widget--large hide-mobile">
              <span className="status-widget__label">Azimuth</span>
              <span className="status-widget__value">{azimuth}</span>
            </div>

            <div className="status-widget status-widget--large hide-mobile">
              <span className="status-widget__label">Current RA</span>
              <span className="status-widget__value">{ra}</span>
            </div>

            <div className="status-widget status-widget--large hide-mobile">
              <span className="status-widget__label">Current DEC</span>
              <span className="status-widget__value">{dec}</span>
            </div>

          </div>
        </div>
      </div>

      <SettingsPanel
        open={settingsOpen}
        closing={settingsClosing}
        onClose={handleCloseSettings}
      />
    </>
  );
};
