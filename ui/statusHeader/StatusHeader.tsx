import React, { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useStatusData } from './hooks/useStatusData';
import { useGuidingRms } from './hooks/useGuidingRms';
import { useSettingsModal } from './hooks/useSettingsModal';
import { describeFrameCopy } from './hooks/useFrameCopyNotice';
import { SettingsPanel } from './SettingsPanel';
import { ObservatorySourceChip } from './ObservatorySourceChip';
import { useAstrometrics } from '../common/context/AstrometricsContext';
import { useOptionalTargetContext } from '../common/context/TargetContext';
import { getEnabledDisplayModes } from '../common/constants/displayFlags';
import { Button } from '../common/components/Button';
import './statusHeader.css';

/** If the server's time left is lower than ours by more than this (ms), snap down to it. */
const EXPOSURE_SNAP_DOWN_THRESHOLD_MS = 1000;

/** If the server's time left is higher than ours by more than this (ms), a new frame started. */
const EXPOSURE_NEW_FRAME_THRESHOLD_MS = 3000;

/** Name of the view the telemetry strip opens. */
const OBSERVATORY_MODE = 'Observatory Manager';

/** Name of the view the Target item opens while the Observatory Manager is showing. */
const IMAGE_PROCESSING_MODE = 'Image Processing';

/**
 * Component representing the application's top status header and settings menu.
 */
export const StatusHeader: React.FC = () => {
  // Hooks
  const {
    telemetry, connected, trackingStatus, telescopeConnection,
    selectedMode, chooseMode
  } = useStatusData();

  const { config, telescope } = useAstrometrics();
  const targetContext = useOptionalTargetContext();

  const { open: settingsOpen, closing: settingsClosing, handleOpen: handleOpenSettings, handleClose: handleCloseSettings } = useSettingsModal();
  const { altitude, azimuth, temperature, humidity, ra, dec, cameraTemperature, cameraStatus } = telemetry;
  const targetName = telescope?.targetName;
  const guidingRms = useGuidingRms(telescope?.guidingHistory);
  const guidingValueText = guidingRms ? `${guidingRms.total.toFixed(2)}″` : '—';
  const guidingTooltip = guidingRms
    ? `RA ${guidingRms.ra.toFixed(2)}″ · Dec ${guidingRms.dec.toFixed(2)}″`
    : 'Guiding RMS: not guiding';

  // New frames are copied to this computer after each exposure. Show that
  // next to the camera state.
  const frameCopy = describeFrameCopy(telescope?.frameCopy, targetName);

  // Real-time camera exposure countdown clock
  const [displayCameraStatus, setDisplayCameraStatus] = useState<string>(cameraStatus || 'Idle');
  const targetEndTimeRef = useRef<number | null>(null);
  // The camera's own picture download (about a second at the end of each frame)
  // is called "Downloading" by the backend. Here it reads "Reading out" so it
  // is not mistaken for the copy of frames to this computer.
  const shownCameraStatus = displayCameraStatus === 'Downloading' ? 'Reading out' : displayCameraStatus;

  useEffect(() => {
    if (!cameraStatus) {
      setDisplayCameraStatus('Idle');
      targetEndTimeRef.current = null;
      return;
    }

    const match = cameraStatus.match(/expos.*?([\d.]+)/i);
    if (match) {
      const remainingSec = parseFloat(match[1]);
      const now = Date.now();
      const newEndTime = now + remainingSec * 1000;

      // The server value is rounded up and a little old, so it is usually a bit
      // higher than our own countdown. Keep the local countdown unless the
      // server clearly disagrees, otherwise the display steps back.
      const differenceMs = targetEndTimeRef.current === null
        ? null
        : newEndTime - targetEndTimeRef.current;
      if (
        differenceMs === null ||
        differenceMs < -EXPOSURE_SNAP_DOWN_THRESHOLD_MS ||
        differenceMs > EXPOSURE_NEW_FRAME_THRESHOLD_MS
      ) {
        targetEndTimeRef.current = newEndTime;
      }
      const localRemainingMs = (targetEndTimeRef.current ?? newEndTime) - now;
      setDisplayCameraStatus(`Exposing (${Math.ceil(Math.max(localRemainingMs, 0) / 1000)}s)`);
    } else {
      targetEndTimeRef.current = null;
      setDisplayCameraStatus(cameraStatus);
    }
  }, [cameraStatus]);

  useEffect(() => {
    if (!targetEndTimeRef.current) return;

    const timer = setInterval(() => {
      if (!targetEndTimeRef.current) return;
      const remainingMs = targetEndTimeRef.current - Date.now();
      if (remainingMs > 0) {
        setDisplayCameraStatus(`Exposing (${Math.ceil(remainingMs / 1000)}s)`);
      } else {
        setDisplayCameraStatus('Downloading');
        targetEndTimeRef.current = null;
      }
    }, 100);

    return () => clearInterval(timer);
  }, [cameraStatus]);

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

  // The dot reflects the telescope mount's own connection (INDI-reported
  // connectionStatus), not just whether our websocket to the backend is
  // alive -- the backend (running on the Pi) can be up with the mount
  // itself still disconnected/powered off. `connected === null` (websocket
  // still connecting) stays "unknown" since telescope status can't be
  // trusted yet; once the websocket is up, a disconnected backend also
  // means the telescope's real status can't be known, so that case reports
  // "disconnected" rather than falsely implying the mount specifically.
  const connModifier = connected === null ? 'unknown' : (connected && telescopeConnection ? 'connected' : 'disconnected');
  const connTxt = connected === null ? 'Checking…' : (connected && telescopeConnection ? 'Connected' : 'Disconnected');
  // With the telescope offline the camera's last state is stale, so show a
  // dash like the other readings instead of "Idle".
  const telescopeOnline = Boolean(connected && telescopeConnection);
  const cameraKnown = telescopeOnline && Boolean(cameraStatus) && !/^unknown$/i.test(cameraStatus ?? '');
  const cameraText = cameraKnown ? shownCameraStatus : '—';
  const cameraExposing = cameraKnown && /expos/i.test(displayCameraStatus);
  const trackVal = trackingStatus || 'Not Tracking';
  const isParked = /park/i.test(String(trackVal));
  const notTrackingRegex = /not track|not-tracking|not tracking/i;
  const trackModifier = isParked
    ? 'parked'
    : notTrackingRegex.test(String(trackVal))
      ? 'not-tracking'
      : (telescopeConnection ? 'connected' : 'disconnected');

  const availableModes = getEnabledDisplayModes(config);

  // The telemetry strip is also a shortcut. In the Observatory Manager the strip
  // does nothing, except that the Target item opens that target in Image Processing.
  const inObservatory = selectedMode === OBSERVATORY_MODE;
  const stripLabel = `Open ${OBSERVATORY_MODE}`;
  const targetLabel = targetName ? `Open ${targetName} in ${IMAGE_PROCESSING_MODE}` : '';

  /** Opens Image Processing with the header's target selected. */
  const handleOpenTargetInImageProcessing = (): void => {
    if (!targetName) return;
    targetContext?.setSelectedTarget(targetName);
    targetContext?.setPendingTarget(targetName);
    chooseMode(IMAGE_PROCESSING_MODE);
  };

  return (
    <>
      <div className="header" role="banner" aria-label="Application header">
        <div className="header__widgets">
          <div className="header__left">
            <div className="header__settings">
              <Button
                variant="ghost"
                size="square"
                className="header__settings-button"
                aria-label="Open settings"
                title="Open settings"
                onClick={handleOpenSettings}
              >
                <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                  <rect x="3" y="6" width="18" height="2" rx="1" />
                  <rect x="3" y="11" width="18" height="2" rx="1" />
                  <rect x="3" y="16" width="18" height="2" rx="1" />
                </svg>
              </Button>
              <div className="mode-selector">
                <Button
                  ref={modeButtonRef}
                  variant="ghost"
                  className={`mode-selector__button ${modeOpen ? 'mode-selector__button--active' : ''}`}
                  onClick={() => setModeOpen((v) => !v)}
                >
                  {selectedMode}
                </Button>
                {modeOpen && menuPosition && createPortal(
                  <div
                    ref={modeMenuRef}
                    className="mode-selector__menu"
                    style={menuPosition}
                  >
                    {availableModes.map((m) => (
                      <div className="mode-selector__row" key={m}>
                        <Button
                          variant="ghost"
                          className={`mode-selector__item ${selectedMode === m ? 'mode-selector__item--selected' : ''}`}
                          onClick={() => handleModeSelect(m)}
                        >
                          {m}
                        </Button>
                        {window.astrometrics?.app?.openDisplayWindow && (
                          <Button
                            variant="ghost"
                            className="mode-selector__popout"
                            onClick={(e) => handleOpenInNewWindow(m, e)}
                            title={`Open ${m} in new window`}
                            aria-label={`Open ${m} in new window`}
                          >
                            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2">
                              <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
                              <polyline points="15 3 21 3 21 9" />
                              <line x1="10" y1="14" x2="21" y2="3" />
                            </svg>
                          </Button>
                        )}
                      </div>
                    ))}
                  </div>,
                  document.body
                )}
              </div>
              {/* Simulation chip: right after the display button, so it never moves that button. */}
              <ObservatorySourceChip />
            </div>
          </div>

          {/* Outside the Observatory Manager the whole strip opens it. The
              mouse click is caught by the strip itself, so a click on any word
              or number works and each item keeps its own tooltip. The hidden
              button below is for the keyboard and screen readers: pressing
              Enter on it sends a click that bubbles up to the strip. */}
          <div
            className={`header__right${inObservatory ? '' : ' header__right--clickable'}`}
            title={inObservatory ? undefined : stripLabel}
            onClick={inObservatory ? undefined : () => chooseMode(OBSERVATORY_MODE)}
          >
            {!inObservatory && (
              <Button
                variant="ghost"
                className="header__strip-button"
                aria-label={stripLabel}
                onClick={(event) => {
                  // The strip also opens the view on a mouse click; stop this click so it is not handled twice.
                  event.stopPropagation();
                  chooseMode(OBSERVATORY_MODE);
                }}
              />
            )}
            <div className="header__telemetry-item" title={`Mount: ${connTxt}, ${trackVal}`}>
              <span className={`header__status-dot header__status-dot--${connModifier}`} aria-hidden="true" />
              <span className={`header__telemetry-value header__telemetry-value--${trackModifier}`}>
                {trackVal}
              </span>
            </div>

            {targetName && (
              <>
                <span className="header__divider" aria-hidden="true" />
                <div className="header__telemetry-item" title="Identified Target Pointing">
                  {inObservatory && (
                    <Button
                      variant="ghost"
                      className="header__target-button"
                      aria-label={targetLabel}
                      title={targetLabel}
                      onClick={handleOpenTargetInImageProcessing}
                    />
                  )}
                  <span className="header__telemetry-label">TARGET</span>
                  <span className={`header__telemetry-value header__telemetry-value--coord header__telemetry-value--target${inObservatory ? ' header__telemetry-value--link' : ''}`}>
                    {targetName}
                  </span>
                </div>
              </>
            )}

            {(displayCameraStatus || cameraTemperature) && (
              <>
                <span className="header__divider hide-mobile" aria-hidden="true" />
                <div className="header__telemetry-item hide-mobile" title="Camera Telemetry">
                  <span className="header__telemetry-label">CAM</span>
                  <span
                    className={`header__telemetry-value ${cameraExposing ? 'header__telemetry-value--exposing' : ''}`}
                  >
                    {cameraText}
                  </span>
                  {frameCopy && (
                    <span className="header__frame-copy" title={frameCopy.description} data-testid="frame-copy" aria-live="polite">
                      <span aria-hidden="true">· {frameCopy.text}</span>
                      <span className="header__visually-hidden">{frameCopy.description}</span>
                    </span>
                  )}
                  {cameraTemperature && cameraTemperature !== '-' && (
                    <span
                      className="header__telemetry-value header__telemetry-value--temperature"
                    >
                      ({cameraTemperature})
                    </span>
                  )}
                </div>
              </>
            )}

            <span className="header__divider hide-mobile" aria-hidden="true" />
            <div
              className="header__telemetry-item hide-mobile"
              title={guidingTooltip}
              aria-label={`Guiding RMS ${guidingRms ? guidingValueText + ', ' + guidingTooltip : 'unavailable'}`}
              role="group"
            >
              <span className="header__telemetry-label" aria-hidden="true">GUIDE</span>
              <span
                className={`header__telemetry-value ${guidingRms ? '' : 'header__telemetry-value--muted'}`}
                data-testid="guiding-rms-value"
              >
                {guidingValueText}
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
