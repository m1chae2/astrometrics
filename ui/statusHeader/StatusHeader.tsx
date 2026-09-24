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

  // Mode Dropdown Logic
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

  /**
   * Spawns a new display window initialized to the selected mode.
   *
   * @param modeToOpen - The display mode to launch in the new window.
   * @param event - Mouse click event.
   */
  const handleOpenInNewWindow = (modeToOpen: string, event: React.MouseEvent) => {
    event.stopPropagation();
    if (window.astrometrics?.app?.openDisplayWindow) {
      window.astrometrics.app.openDisplayWindow(modeToOpen);
    }
    setModeOpen(false);
  };

  const connModifier = connected === null ? 'unknown' : (connected ? 'connected' : 'disconnected');
  const connTxt = connected === null ? 'Checking…' : (connected ? 'Connected' : 'Disconnected');
  const trackVal = trackingStatus || 'Not Tracking';
  const isParked = /park/i.test(String(trackVal));
  const notTrackingRegex = /not track|not-tracking|not tracking/i;
  const trackModifier = isParked
    ? 'parked'
    : notTrackingRegex.test(String(trackVal))
      ? 'not-tracking'
      : (telescopeConnection ? 'connected' : 'disconnected');

  const availableModes = [
    'Image Viewer',
    'Image Processing',
    'Command Console',
    ...(config['Frontend']?.['enable_astronomy'] === 'true' ? ['Astronomy Manager'] : []),
    ...(config['Frontend']?.['enable_planetarium'] === 'true' ? ['Planetarium'] : []),
    ...(config['Frontend']?.['enable_observatory'] === 'true' ? ['Observatory Manager'] : []),
    ...(config['Frontend']?.['enable_observation'] === 'true' ? ['Observation Manager'] : []),
  ];

  return (
    <>
      <div className="header" role="banner" aria-label="Application header">
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
                    {availableModes.map((m) => (
                      <div className="mode-selector__row" key={m}>
                        <button
                          className={`mode-selector__item ${selectedMode === m ? 'mode-selector__item--selected' : ''}`}
                          onClick={() => handleModeSelect(m)}
                          type="button"
                        >
                          {m}
                        </button>
                        {window.astrometrics?.app?.openDisplayWindow && (
                          <button
                            className="mode-selector__popout"
                            onClick={(e) => handleOpenInNewWindow(m, e)}
                            title={`Open ${m} in new window`}
                            aria-label={`Open ${m} in new window`}
                            type="button"
                          >
                            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2">
                              <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
                              <polyline points="15 3 21 3 21 9" />
                              <line x1="10" y1="14" x2="21" y2="3" />
                            </svg>
                          </button>
                        )}
                      </div>
                    ))}
                  </div>,
                  document.body
                )}
              </div>
            </div>
          </div>

          <div className="header__right">
            <div className="header__telemetry-item" title={`Mount: ${connTxt}, ${trackVal}`}>
              <span className={`header__status-dot header__status-dot--${connModifier}`} aria-hidden="true" />
              <span className={`header__telemetry-value header__telemetry-value--${trackModifier}`}>
                {trackVal}
              </span>
            </div>

            <span className="header__divider" aria-hidden="true" />

            <div className="header__telemetry-item header__telemetry-item--coords" title="Equatorial Coordinates">
              <span className="header__telemetry-label">RA</span>
              <span className="header__telemetry-value header__telemetry-value--coord">{ra}</span>
              <span className="header__telemetry-label" style={{ marginLeft: '4px' }}>DEC</span>
              <span className="header__telemetry-value header__telemetry-value--coord">{dec}</span>
            </div>

            <span className="header__divider hide-mobile" aria-hidden="true" />

            <div className="header__telemetry-item header__telemetry-item--coords hide-mobile" title="Horizontal Coordinates">
              <span className="header__telemetry-label">ALT</span>
              <span className="header__telemetry-value header__telemetry-value--coord">{altitude}</span>
              <span className="header__telemetry-label" style={{ marginLeft: '4px' }}>AZ</span>
              <span className="header__telemetry-value header__telemetry-value--coord">{azimuth}</span>
            </div>

            <span className="header__divider hide-mobile" aria-hidden="true" />

            <div className="header__telemetry-item hide-mobile" title="Ambient Temperature">
              <svg className="header__telemetry-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                <path d="M12 2a3 3 0 0 0-3 3v7a5 5 0 1 0 6 0V5a3 3 0 0 0-3-3z" />
              </svg>
              <span className="header__telemetry-value">
                {temperature}
                <span className="header__telemetry-unit">°C</span>
              </span>
            </div>

            <div className="header__telemetry-item hide-mobile" title="Relative Humidity">
              <svg className="header__telemetry-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                <path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z" />
              </svg>
              <span className="header__telemetry-value">
                {humidity}
                <span className="header__telemetry-unit">%</span>
              </span>
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
