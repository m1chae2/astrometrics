/**
 * @file WorkspaceTable.tsx
 * @description Component displaying active workspace variables (MATLAB-style Workspace viewer) plus pinned system objects.
 */
import React from 'react';

export interface WorkspaceVariable {
  name: string;
  type: string;
  shape?: string | null;
  dtype?: string | null;
  size_bytes: number;
  summary: string;
}

interface WorkspaceTableProps {
  variables: WorkspaceVariable[];
  onSelectVariable?: (variable: WorkspaceVariable) => void;
  onRefresh?: () => void;
}

const PINNED_SYSTEM_OBJECTS = [
  { name: 'targets', type: 'TargetService', desc: 'Target catalog & frame access' },
  { name: 'stars', type: 'StellarService', desc: 'Stellar catalog, photometry, & spectra' },
  { name: 'jobs', type: 'JobService', desc: 'Pipeline jobs & logs database' },
  { name: 'telescope', type: 'TelescopeService', desc: 'Mount motion, parking, & status' },
  { name: 'imaging', type: 'ImagingService', desc: 'Camera capture sequences' },
  { name: 'guiding', type: 'GuidingService', desc: 'Auto-guiding loop & calibration' },
  { name: 'np', type: 'module', desc: 'NumPy scientific computing' },
  { name: 'plt', type: 'module', desc: 'Matplotlib headless figure plotting' },
];

export const WorkspaceTable: React.FC<WorkspaceTableProps> = ({
  variables,
  onSelectVariable,
  onRefresh,
}) => {
  return (
    <div className="workspace-table-container">
      <div className="console-panel__header">
        <span className="console-panel__title">
          Workspace ({variables.length + PINNED_SYSTEM_OBJECTS.length})
        </span>
        <div className="console-panel__actions">
          {onRefresh && (
            <button
              className="editor-btn"
              style={{ padding: '2px 6px', fontSize: '11px' }}
              onClick={onRefresh}
              title="Refresh Workspace"
              type="button"
            >
              Refresh
            </button>
          )}
        </div>
      </div>

      <div className="console-panel__body">
        <table className="workspace-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Type</th>
              <th>Shape / Size</th>
              <th>Value / Description</th>
            </tr>
          </thead>
          <tbody>
            {/* Pinned system objects */}
            {PINNED_SYSTEM_OBJECTS.map((obj) => (
              <tr key={obj.name} className="pinned" title="Pinned System Object">
                <td style={{ fontWeight: 600 }}>{obj.name}</td>
                <td>{obj.type}</td>
                <td style={{ color: 'var(--text-dim)' }}>Built-in</td>
                <td style={{ color: 'var(--text-dim)' }}>{obj.desc}</td>
              </tr>
            ))}

            {/* Dynamic user variables */}
            {variables.map((v) => (
              <tr
                key={v.name}
                onClick={() => onSelectVariable?.(v)}
                style={{ cursor: onSelectVariable ? 'pointer' : 'default' }}
              >
                <td style={{ fontWeight: 500 }}>{v.name}</td>
                <td style={{ color: 'var(--term-yellow)' }}>{v.type}</td>
                <td>{v.shape ? `${v.shape} (${v.dtype || ''})` : `${v.size_bytes} B`}</td>
                <td title={v.summary}>{v.summary}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};
