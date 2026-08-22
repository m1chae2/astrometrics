/**
 * @module PlanetariumDateTimeModal
 * @fileoverview Date and time picker modal for the Planetarium viewport.
 *
 * Provides two input modes: a Gregorian date/time spinner and a Julian Day
 * number input. Styled to match the Stellarium Web interface.
 *
 */

import React, { useState, useEffect } from 'react';

/**
 * Props for PlanetariumDateTimeModal.
 */
interface Props {
  /** Whether the modal is rendered. */
  isOpen: boolean;
  /** Callback to dismiss the modal. */
  onClose: () => void;
  /** The currently active simulation date. */
  currentDate: Date;
  /** Callback invoked with the new date when any field changes. */
  onDateChange: (date: Date) => void;
  /** Callback invoked when the user resets to the live system clock. */
  onResetToNow: () => void;
}

/**
 * Converts a JavaScript Date to a Julian Date number.
 *
 * Uses the Unix epoch offset: JD 2440587.5 corresponds to 1970-01-01T00:00:00Z.
 *
 * @param {Date} date - The Gregorian calendar date to convert.
 * @returns {number} The corresponding Julian Date.
 */
const toJulianDate = (date: Date): number => {
  return (date.getTime() / 86400000.0) + 2440587.5;
};

/**
 * Converts a Julian Date number back to a JavaScript Date.
 *
 * @param {number} julianDay - Julian Date to convert.
 * @returns {Date} The corresponding Gregorian date.
 */
const fromJulianDate = (julianDay: number): Date => {
  return new Date((julianDay - 2440587.5) * 86400000.0);
};

/**
 * Tabbed modal for adjusting the planetarium simulation date and time.
 *
 * The "Date and Time" tab presents Gregorian year/month/day and hour/minute/second
 * spinners. The "Julian Day" tab presents a single Julian Day Number field with
 * +/- 1.0 day step buttons. All edits are propagated immediately via onDateChange.
 *
 * @func PlanetariumDateTimeModal
 * @param {Props} props - Component props.
 * @returns {React.ReactElement | null} The rendered modal, or null when isOpen is false.
 */
export const PlanetariumDateTimeModal: React.FC<Props> = ({
  isOpen,
  onClose,
  currentDate,
  onDateChange,
  onResetToNow
}) => {
  const [activeTab, setActiveTab] = useState<'datetime' | 'julian'>('datetime');

  // Date states
  const [year, setYear] = useState<number>(currentDate.getFullYear());
  const [month, setMonth] = useState<number>(currentDate.getMonth() + 1);
  const [day, setDay] = useState<number>(currentDate.getDate());

  // Time states
  const [hours, setHours] = useState<number>(currentDate.getHours());
  const [minutes, setMinutes] = useState<number>(currentDate.getMinutes());
  const [seconds, setSeconds] = useState<number>(currentDate.getSeconds());

  // Julian Date state
  const [julianDay, setJulianDay] = useState<number>(toJulianDate(currentDate));

  // Sync state from props
  useEffect(() => {
    setYear(currentDate.getFullYear());
    setMonth(currentDate.getMonth() + 1);
    setDay(currentDate.getDate());
    setHours(currentDate.getHours());
    setMinutes(currentDate.getMinutes());
    setSeconds(currentDate.getSeconds());
    setJulianDay(toJulianDate(currentDate));
  }, [currentDate]);

  if (!isOpen) return null;

  /**
   * Validates and applies a Gregorian date/time change.
   * Clamps month to 1–12, day to 1–31, hours to 0–23, minutes/seconds to 0–59.
   *
   * @param {number} newYear - Full year (e.g. 2026).
   * @param {number} newMonth - Month (1-indexed).
   * @param {number} newDay - Day of month.
   * @param {number} newHour - Hour of day (0–23).
   * @param {number} newMinute - Minute (0–59).
   * @param {number} newSecond - Second (0–59).
   * @returns {void}
   */
  const handleGregorianChange = (newYear: number, newMonth: number, newDay: number, newHour: number, newMinute: number, newSecond: number) => {
    const validatedMonth = Math.max(1, Math.min(12, newMonth));
    const validatedDay = Math.max(1, Math.min(31, newDay));
    const validatedHour = Math.max(0, Math.min(23, newHour));
    const validatedMin = Math.max(0, Math.min(59, newMinute));
    const validatedSec = Math.max(0, Math.min(59, newSecond));

    const date = new Date(newYear, validatedMonth - 1, validatedDay, validatedHour, validatedMin, validatedSec);
    onDateChange(date);
  };

  /**
   * Applies a Julian Day Number change and propagates the equivalent Gregorian date.
   *
   * @param {number} newJD - New Julian Day Number.
   * @returns {void}
   */
  const handleJulianChange = (newJD: number) => {
    setJulianDay(newJD);
    const date = fromJulianDate(newJD);
    onDateChange(date);
  };

  return (
    <div className="planetarium-datetime-modal">
      {/* Header */}
      <div className="planetarium-datetime-modal__header">
        <div className="planetarium-datetime-modal__title">Date and Time</div>
        <button className="planetarium-datetime-modal__close" onClick={onClose}>&times;</button>
      </div>

      {/* Tabs */}
      <div className="planetarium-datetime-modal__tabs">
        <button
          className="planetarium-datetime-modal__tab"
          onClick={() => setActiveTab('datetime')}
          style={{
            background: activeTab === 'datetime' ? 'rgba(255,255,255,0.1)' : 'transparent',
            fontWeight: activeTab === 'datetime' ? 'bold' : 'normal',
            borderBottom: activeTab === 'datetime' ? '2px solid #5c9eff' : 'none',
          }}
        >
          Date and Time
        </button>
        <button
          className="planetarium-datetime-modal__tab"
          onClick={() => setActiveTab('julian')}
          style={{
            background: activeTab === 'julian' ? 'rgba(255,255,255,0.1)' : 'transparent',
            fontWeight: activeTab === 'julian' ? 'bold' : 'normal',
            borderBottom: activeTab === 'julian' ? '2px solid #5c9eff' : 'none',
          }}
        >
          Julian Day
        </button>
      </div>

      {/* Content */}
      <div className="planetarium-datetime-modal__content">
        {activeTab === 'datetime' ? (
          <div className="planetarium-datetime-modal__section-group">
            {/* Date Section */}
            <div className="planetarium-datetime-modal__section">
              <div className="planetarium-datetime-modal__section-label">Date</div>
              <div className="planetarium-datetime-modal__spinner-group">
                {/* Year spinner */}
                <div className="planetarium-datetime-modal__spinner">
                  <button className="planetarium-datetime-modal__arrow-btn" onClick={() => handleGregorianChange(year + 1, month, day, hours, minutes, seconds)}>▲</button>
                  <span className="planetarium-datetime-modal__val">{year}</span>
                  <button className="planetarium-datetime-modal__arrow-btn" onClick={() => handleGregorianChange(year - 1, month, day, hours, minutes, seconds)}>▼</button>
                </div>
                <span className="planetarium-datetime-modal__separator">-</span>
                {/* Month spinner */}
                <div className="planetarium-datetime-modal__spinner">
                  <button className="planetarium-datetime-modal__arrow-btn" onClick={() => handleGregorianChange(year, month + 1, day, hours, minutes, seconds)}>▲</button>
                  <span className="planetarium-datetime-modal__val">{month}</span>
                  <button className="planetarium-datetime-modal__arrow-btn" onClick={() => handleGregorianChange(year, month - 1, day, hours, minutes, seconds)}>▼</button>
                </div>
                <span className="planetarium-datetime-modal__separator">-</span>
                {/* Day spinner */}
                <div className="planetarium-datetime-modal__spinner">
                  <button className="planetarium-datetime-modal__arrow-btn" onClick={() => handleGregorianChange(year, month, day + 1, hours, minutes, seconds)}>▲</button>
                  <span className="planetarium-datetime-modal__val">{day}</span>
                  <button className="planetarium-datetime-modal__arrow-btn" onClick={() => handleGregorianChange(year, month, day - 1, hours, minutes, seconds)}>▼</button>
                </div>
              </div>
            </div>

            {/* Vertical Divider */}
            <div className="planetarium-datetime-modal__divider" />

            {/* Time Section */}
            <div className="planetarium-datetime-modal__section">
              <div className="planetarium-datetime-modal__section-label">Time</div>
              <div className="planetarium-datetime-modal__spinner-group">
                {/* Hours spinner */}
                <div className="planetarium-datetime-modal__spinner">
                  <button className="planetarium-datetime-modal__arrow-btn" onClick={() => handleGregorianChange(year, month, day, hours + 1, minutes, seconds)}>▲</button>
                  <span className="planetarium-datetime-modal__val">{String(hours).padStart(2, '0')}</span>
                  <button className="planetarium-datetime-modal__arrow-btn" onClick={() => handleGregorianChange(year, month, day, hours - 1, minutes, seconds)}>▼</button>
                </div>
                <span className="planetarium-datetime-modal__separator">:</span>
                {/* Minutes spinner */}
                <div className="planetarium-datetime-modal__spinner">
                  <button className="planetarium-datetime-modal__arrow-btn" onClick={() => handleGregorianChange(year, month, day, hours, minutes + 1, seconds)}>▲</button>
                  <span className="planetarium-datetime-modal__val">{String(minutes).padStart(2, '0')}</span>
                  <button className="planetarium-datetime-modal__arrow-btn" onClick={() => handleGregorianChange(year, month, day, hours, minutes - 1, seconds)}>▼</button>
                </div>
                <span className="planetarium-datetime-modal__separator">:</span>
                {/* Seconds spinner */}
                <div className="planetarium-datetime-modal__spinner">
                  <button className="planetarium-datetime-modal__arrow-btn" onClick={() => handleGregorianChange(year, month, day, hours, minutes, seconds + 1)}>▲</button>
                  <span className="planetarium-datetime-modal__val">{String(seconds).padStart(2, '0')}</span>
                  <button className="planetarium-datetime-modal__arrow-btn" onClick={() => handleGregorianChange(year, month, day, hours, minutes, seconds - 1)}>▼</button>
                </div>
              </div>
            </div>
          </div>
        ) : (
          <div className="planetarium-datetime-modal__jd-section">
            <div className="planetarium-datetime-modal__section-label">Julian Day Number</div>
            <div className="planetarium-datetime-modal__spinner-group">
              <button className="planetarium-datetime-modal__arrow-btn" style={{ fontSize: '16px' }} onClick={() => handleJulianChange(julianDay - 1.0)}>◀</button>
              <div className="planetarium-datetime-modal__jd-display">
                {julianDay.toFixed(5)}
              </div>
              <button className="planetarium-datetime-modal__arrow-btn" style={{ fontSize: '16px' }} onClick={() => handleJulianChange(julianDay + 1.0)}>▶</button>
            </div>
            <div className="planetarium-datetime-modal__jd-hint">Adjust by +/- 1.0 day increments</div>
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="planetarium-datetime-modal__footer">
        <button
          className="planetarium-datetime-modal__reset-btn"
          onClick={onResetToNow}
        >
          Reset to Now
        </button>
      </div>
    </div>
  );
};
