/**
 * @file MetricsScorecard.tsx
 * @description Interactive scorecard displaying input and output quality metrics for a pipeline run, with load-into-REPL action.
 */
import React from 'react';
import { ProcessingJob } from '../common/types/backendTypes';

interface MetricsScorecardProps {
  job: ProcessingJob | null;
  onLoadIntoRepl: (jobId: string) => void;
}

export const MetricsScorecard: React.FC<MetricsScorecardProps> = ({
  job,
  onLoadIntoRepl,
}) => {
  if (!job) {
    return (
      <div style={{ padding: '24px', textAlign: 'center', color: 'var(--text-dim)' }}>
        Select a pipeline run from the sidebar to inspect its quality metrics.
      </div>
    );
  }

  const inputMetrics = job.inputMetrics || {};
  const outputMetrics = job.outputMetrics || {};

  const renderMetricValue = (val: any) => {
    if (typeof val === 'number') {
      return Number.isInteger(val) ? val.toLocaleString() : val.toFixed(3);
    }
    if (typeof val === 'boolean') {
      return val ? 'True' : 'False';
    }
    if (typeof val === 'object' && val !== null) {
      return JSON.stringify(val);
    }
    return String(val);
  };

  return (
    <div className="metrics-scorecard">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h3 style={{ margin: 0, fontSize: '16px', color: 'var(--text-primary)' }}>
            Run Metrics: {job.targetId} ({job.jobType})
          </h3>
          <div style={{ fontSize: '11px', color: 'var(--text-dim)', marginTop: '4px' }}>
            Job ID: <code>{job.id}</code> &bull; Status: <span style={{ color: 'var(--ubuntu-green)' }}>{job.status}</span>
          </div>
        </div>
        <button
          className="editor-btn editor-btn--primary"
          onClick={() => onLoadIntoRepl(job.id)}
          title="Inject 'run' and 'target' objects into Python REPL console scope"
          type="button"
        >
          Load Run into REPL
        </button>
      </div>

      <div className="metrics-section">
        <div className="metrics-section__header">Input Quality Diagnostics</div>
        {Object.keys(inputMetrics).length === 0 ? (
          <div style={{ fontSize: '12px', color: 'var(--text-dim)', padding: '6px 0' }}>
            No input metrics recorded for this run.
          </div>
        ) : (
          <div className="metrics-grid">
            {Object.entries(inputMetrics).map(([key, val]) => (
              <div key={key} className="metric-card">
                <span className="metric-card__label">{key.replace(/_/g, ' ').toUpperCase()}</span>
                <span className="metric-card__value">{renderMetricValue(val)}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="metrics-section">
        <div className="metrics-section__header">Output Quality Results</div>
        {Object.keys(outputMetrics).length === 0 ? (
          <div style={{ fontSize: '12px', color: 'var(--text-dim)', padding: '6px 0' }}>
            No output metrics generated yet (job may still be processing or completed without metric payloads).
          </div>
        ) : (
          <div className="metrics-grid">
            {Object.entries(outputMetrics).map(([key, val]) => (
              <div key={key} className="metric-card">
                <span className="metric-card__label">{key.replace(/_/g, ' ').toUpperCase()}</span>
                <span className="metric-card__value" style={{ color: 'var(--term-cyan)' }}>
                  {renderMetricValue(val)}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
