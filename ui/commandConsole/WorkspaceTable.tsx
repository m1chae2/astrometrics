/**
 * @file WorkspaceTable.tsx
 * @description The Command Console's Workspace table. It lists the names the console has ready
 * at the start (such as `astrometrics`, `selection` and `show`) at the top, then the variables
 * your code made. The columns keep fixed shares of the width, so a long value never squeezes the
 * Name and Type columns; a cut-off cell shows its full text on hover.
 */
import React from 'react';
import type { ActionRegistry } from '../common/services/backendApi';

/** One variable your code made, as `terminal:get_workspace` lists it. */
export interface WorkspaceVariable {
  name: string;
  type: string;
  shape?: string | null;
  dtype?: string | null;
  size_bytes: number;
  summary: string;
}

/** One name the console has ready at the start, as `terminal:get_preloaded_objects` lists it. */
export type PreloadedObject = ActionRegistry['terminal:get_preloaded_objects']['response'][number];

/** Props for WorkspaceTable. */
interface WorkspaceTableProps {
  /** The variables your code made. */
  variables: WorkspaceVariable[];
  /** The names the console has ready at the start. */
  preloadedObjects: PreloadedObject[];
}

/**
 * Builds the text of the Shape / Size cell for one variable.
 *
 * @param variable The variable.
 * @returns The array shape and data type, or the size in bytes.
 */
function shapeOrSize(variable: WorkspaceVariable): string {
  if (variable.shape) return `${variable.shape} (${variable.dtype || ''})`;
  return `${variable.size_bytes} B`;
}

/**
 * Shows the Workspace table.
 *
 * @param props See `WorkspaceTableProps`.
 * @returns The table.
 */
export const WorkspaceTable: React.FC<WorkspaceTableProps> = ({ variables, preloadedObjects }) => (
  <table className="workspace-table">
    <colgroup>
      <col className="workspace-table__col--name" />
      <col className="workspace-table__col--type" />
      <col className="workspace-table__col--size" />
      <col />
    </colgroup>
    <thead>
      <tr>
        <th>Name</th>
        <th>Type</th>
        <th>Shape / Size</th>
        <th>Value / Description</th>
      </tr>
    </thead>
    <tbody>
      {preloadedObjects.map((preloaded) => (
        <tr
          key={preloaded.name}
          className={`workspace-table__row--preloaded${preloaded.available ? '' : ' workspace-table__row--unavailable'}`}
          title={preloaded.available ? 'Ready when the console starts' : 'Not available in this install'}
        >
          <td className="workspace-table__name" title={preloaded.name}>{preloaded.name}</td>
          <td title={preloaded.type}>{preloaded.type}</td>
          <td className="workspace-table__muted">{preloaded.available ? 'Built-in' : 'Not available'}</td>
          <td className="workspace-table__muted" title={preloaded.description}>{preloaded.description}</td>
        </tr>
      ))}
      {variables.map((variable) => (
        <tr key={variable.name}>
          <td className="workspace-table__name" title={variable.name}>{variable.name}</td>
          <td className="workspace-table__type" title={variable.type}>{variable.type}</td>
          <td title={shapeOrSize(variable)}>{shapeOrSize(variable)}</td>
          <td title={variable.summary}>{variable.summary}</td>
        </tr>
      ))}
    </tbody>
  </table>
);
