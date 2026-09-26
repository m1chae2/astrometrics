/**
 * @fileoverview Bottom workstation status bar for Astrometrics.
 * Displays real-time telescope mount coordinates (RA, Dec, Alt, Az),
 * environmental sensors (temperature, humidity), and backend connection status.
 */

import React from 'react';
import { useAstrometrics } from '../common/context/AstrometricsContext';
import './statusBar.css';

/**
 * Normalizes an optional telemetry string, replacing missing or unknown values with a dash.
 *
 * @param value Raw telemetry string or undefined.
 * @returns Sanitized string value.
 */
function normalizeTelemetry(value?: string): string {
  if (!value || /^unknown$/i.test(value)) {
    return '-';
  }
  return value;
}

/**
 * Rounds fractional coordinate seconds and numeric values to whole units for steady display.
 *
 * @param value Raw coordinate or measurement string.
 * @returns Cleaned string formatted with whole numbers.
 */
function formatTelemetryValue(value: string): string {
  if (!value || value === '-') {
    return value;
  }

  // Handle celestial coordinate strings: [+-]XX° YY' ZZ.ZZ"
  const coordRegex = /^([+-]?\d+°\s*)(\d+)['′]\s*(\d+(?:\.\d+)?)(["″])$/;
  const coordMatch = value.match(coordRegex);
  if (coordMatch) {
    const [, degPrefix, minutes, seconds, suffix] = coordMatch;
    const roundedSec = Math.round(parseFloat(seconds));
    const padSec = String(roundedSec).padStart(2, '0');
    const padMin = String(parseInt(minutes, 10)).padStart(2, '0');
    return `${degPrefix}${padMin}' ${padSec}${suffix}`;
  }

  // Handle plain numeric strings (e.g. temperature or humidity)
  const numericValue = parseFloat(value);
  if (!isNaN(numericValue) && /^[+-]?\d+(\.\d+)?$/.test(value.trim())) {
    return Math.round(numericValue).toString();
  }

  return value;
}

/**
 * Docked bottom workstation status bar rendering observatory telemetry in tabular monospace figures.
 *
 * @returns The rendered status bar element.
 */
export const StatusBar: React.FC = () => {
  const { telescope, connected: isSocketConnected } = useAstrometrics();

  const ra = formatTelemetryValue(normalizeTelemetry(telescope.ra));
  const dec = formatTelemetryValue(normalizeTelemetry(telescope.dec));
  const alt = formatTelemetryValue(normalizeTelemetry(telescope.altitude));
  const az = formatTelemetryValue(normalizeTelemetry(telescope.azimuth));

  const rawTemp = normalizeTelemetry(telescope.temperature).replace(/\s*°C$/, '');
  const temperature = formatTelemetryValue(rawTemp);

  const rawHumidity = normalizeTelemetry(telescope.humidity).replace(/\s*%$/, '');
  const humidity = formatTelemetryValue(rawHumidity);

  const isTelescopeConnected = telescope.connectionStatus === 'Connected';
  const trackingStatus = telescope.trackingStatus || 'Not Tracking';

  return (
    <footer className="status-bar" role="contentinfo" aria-label="Observatory status bar">
      <div className="status-bar__left">
        <div className="status-bar__item" title={`Mount: ${telescope.connectionStatus || 'Disconnected'}`}>
          <span
            className={`status-bar__dot status-bar__dot--${
              isTelescopeConnected ? 'connected' : 'disconnected'
            }`}
            aria-hidden="true"
          />
          <span className="status-bar__value">{trackingStatus}</span>
        </div>

        <span className="status-bar__divider" aria-hidden="true" />

        <div className="status-bar__item status-bar__item--coords" title="Equatorial Coordinates">
          <span className="status-bar__label">RA</span>
          <span className="status-bar__value status-bar__value--coord">{ra}</span>
          <span className="status-bar__label" style={{ marginLeft: '6px' }}>DEC</span>
          <span className="status-bar__value status-bar__value--coord">{dec}</span>
        </div>

        <span className="status-bar__divider" aria-hidden="true" />

        <div className="status-bar__item status-bar__item--coords" title="Horizontal Coordinates">
          <span className="status-bar__label">ALT</span>
          <span className="status-bar__value status-bar__value--coord">{alt}</span>
          <span className="status-bar__label" style={{ marginLeft: '6px' }}>AZ</span>
          <span className="status-bar__value status-bar__value--coord">{az}</span>
        </div>
      </div>

      <div className="status-bar__right">
        <div className="status-bar__item" title="Ambient Temperature">
          <svg className="status-bar__icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <path d="M12 2a3 3 0 0 0-3 3v7a5 5 0 1 0 6 0V5a3 3 0 0 0-3-3z" />
          </svg>
          <span className="status-bar__value">
            {temperature}
            <span className="status-bar__unit">°C</span>
          </span>
        </div>

        <div className="status-bar__item" title="Relative Humidity">
          <svg className="status-bar__icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
            <path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z" />
          </svg>
          <span className="status-bar__value">
            {humidity}
            <span className="status-bar__unit">%</span>
          </span>
        </div>

        <span className="status-bar__divider" aria-hidden="true" />

        <div className="status-bar__item" title={`Backend Server: ${isSocketConnected ? 'Online' : 'Offline'}`}>
          <span
            className={`status-bar__dot status-bar__dot--${
              isSocketConnected ? 'connected' : 'disconnected'
            }`}
            aria-hidden="true"
          />
          <span className="status-bar__label">
            {isSocketConnected ? 'ONLINE' : 'OFFLINE'}
          </span>
        </div>
      </div>
    </footer>
  );
};

export default StatusBar;
