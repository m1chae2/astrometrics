/**
 * @fileoverview Compact time range control for AstronomyDisplay: one slider
 * with a handle for each end of the range, and a line saying what is selected.
 */

import React from 'react';
import '../styles/timeline.css';

interface TimelineRangeSelectorProps {
  /** Every measurement time, oldest first. */
  timestamps: string[];
  /** Index in `timestamps` where the selected range starts. */
  startIdx: number;
  /** Index in `timestamps` where the selected range ends. */
  endIdx: number;
  /** Called with the new start and end indexes when either handle moves. */
  onChange: (start: number, end: number) => void;
}

/**
 * Formats a timestamp as a short local date and time.
 * @param timestamp The timestamp to format.
 * @returns For example "May 24, 04:46 AM".
 */
function formatTime(timestamp: string): string {
  return new Date(timestamp).toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/**
 * Renders a two-handle slider that picks which stretch of time is shown.
 *
 * The handles move over measurement numbers, not clock time, so each step
 * lands on a real measurement.
 */
export const TimelineRangeSelector: React.FC<TimelineRangeSelectorProps> = ({
  timestamps,
  startIdx,
  endIdx,
  onChange,
}) => {
  const lastIndex = Math.max(0, timestamps.length - 1);

  const handleStartChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    onChange(Math.min(Number(event.target.value), endIdx), endIdx);
  };

  const handleEndChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    onChange(startIdx, Math.max(Number(event.target.value), startIdx));
  };

  if (timestamps.length === 0) {
    return <p className="empty-text">No time-series data available.</p>;
  }

  const startFraction = lastIndex > 0 ? startIdx / lastIndex : 0;
  const endFraction = lastIndex > 0 ? endIdx / lastIndex : 1;
  const selectedCount = endIdx - startIdx + 1;

  return (
    <div className="timeline-selector">
      <div className="timeline-summary">
        <div className="timeline-summary__range">
          {formatTime(timestamps[startIdx])} → {formatTime(timestamps[endIdx])}
        </div>
        <div className="timeline-summary__count">
          {selectedCount} of {timestamps.length} time points
        </div>
      </div>

      <div className="timeline-range">
        <div className="timeline-range__track">
          <div
            className="timeline-range__fill"
            style={{
              left: `calc(${startFraction} * (100% - var(--timeline-thumb-size)) + var(--timeline-thumb-size) / 2)`,
              width: `calc(${endFraction - startFraction} * (100% - var(--timeline-thumb-size)))`,
            }}
          />
        </div>
        {/* The two sliders sit on top of each other. When both handles are near
            the right end, the start handle goes on top so it can still be dragged left. */}
        <input
          type="range"
          aria-label="Start of time range"
          min={0}
          max={lastIndex}
          value={startIdx}
          onChange={handleStartChange}
          style={{ zIndex: startFraction > 0.5 ? 3 : 1 }}
        />
        <input
          type="range"
          aria-label="End of time range"
          min={0}
          max={lastIndex}
          value={endIdx}
          onChange={handleEndChange}
          style={{ zIndex: 2 }}
        />
      </div>
    </div>
  );
};
