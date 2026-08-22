import React from 'react';
import '../styles/timeline.css';

interface TimelineRangeSelectorProps {
  timestamps: string[];
  startIdx: number;
  endIdx: number;
  onChange: (start: number, end: number) => void;
}

export const TimelineRangeSelector: React.FC<TimelineRangeSelectorProps> = ({ timestamps, startIdx, endIdx, onChange }) => {
  const max = Math.max(0, timestamps.length - 1);

  const handleStartChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = Number(e.target.value);
    onChange(Math.min(val, endIdx), endIdx);
  };

  const handleEndChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = Number(e.target.value);
    onChange(startIdx, Math.max(val, startIdx));
  };

  const formatTime = (t: string) => {
    return new Date(t).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  };

  if (timestamps.length === 0) {
    return <p className="empty-text">No time-series data available.</p>;
  }

  return (
    <div className="timeline-selector">
      <div className="timeline-sliders">
        <label className="slider-label">
          <div className="slider-label-text">Start: {formatTime(timestamps[startIdx])}</div>
          <input type="range" min={0} max={max} value={startIdx} onChange={handleStartChange} />
        </label>
        <label className="slider-label">
          <div className="slider-label-text">End: {formatTime(timestamps[endIdx])}</div>
          <input type="range" min={0} max={max} value={endIdx} onChange={handleEndChange} />
        </label>
      </div>

      <div className="timeline-visual">
        <div className="timeline-points">
          <div className="timeline-track">
             <div
               className="timeline-highlight"
               style={{
                 top: `${max > 0 ? (startIdx / max) * 100 : 0}%`,
                 height: `${max > 0 ? ((endIdx - startIdx) / max) * 100 : 100}%`
               }}
             />
          </div>
          {timestamps.map((t, i) => {
            const isActive = i >= startIdx && i <= endIdx;
            return (
              <div key={t} className={`timeline-point ${isActive ? 'active' : ''}`}>
                <div className="timeline-dot" />
                <span className="timeline-time">{formatTime(t)}</span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};
