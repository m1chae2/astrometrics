import React from 'react';

export interface FrameFilterGroup {
  filterName: string;
  exposures: { duration: number; count: number }[];
}

export interface TargetDetailsProps {
  catalogId?: string;
  commonName?: string;
  camera?: string;
  telescope?: string;
  expTime?: string;
  filterGroups?: FrameFilterGroup[];
  ra?: string;
  dec?: string;
  averageValue?: string;
  onCatalogIdChange?: (value: string) => void;
  onCommonNameChange?: (value: string) => void;
}

/** Pulls the numbers out of a sexagesimal string such as "10h 6m 41s", "10:6:41" or "10 6 41". */
function sexagesimalParts(str: string): string[] {
  return str.match(/-?\d+(?:\.\d+)?/g) ?? [];
}

/**
 * Formats a right ascension (RA) as hours, minutes and seconds ("10h 6m 41s").
 *
 * RA is measured in hours, so it never gets degree symbols. A value that
 * already uses "h" is returned unchanged. A lone number has no known unit
 * (hours or degrees), so it is also returned unchanged.
 */
export function formatRaString(val: string | number | null | undefined): string {
  if (val == null || val === '') return '—';
  const str = String(val).trim();
  if (!str) return '—';

  if (/h/i.test(str)) return str;

  const parts = sexagesimalParts(str);
  if (parts.length >= 3) {
    return `${parts[0]}h ${parts[1]}m ${Math.round(parseFloat(parts[2]))}s`;
  }
  if (parts.length === 2) {
    return `${parts[0]}h ${parts[1]}m 0s`;
  }
  return str;
}

/**
 * Formats a declination as degrees, arcminutes and arcseconds ("-5° 3′ 2″")
 * if it is not already written that way.
 */
export function formatDecString(val: string | number | null | undefined): string {
  if (val == null || val === '') return '—';
  const str = String(val).trim();
  if (!str) return '—';

  // If already contains symbols
  if (str.includes('°')) {
    return str;
  }

  const matches = sexagesimalParts(str);
  if (matches.length === 0) return str;

  if (matches.length >= 3) {
    const d = matches[0];
    const m = matches[1];
    const s = Math.round(parseFloat(matches[2]));
    return `${d}° ${m}′ ${s}″`;
  } else if (matches.length === 2) {
    return `${matches[0]}° ${matches[1]}′ 0″`;
  } else if (matches.length === 1) {
    const num = parseFloat(matches[0]);
    if (!isNaN(num)) {
      const d = Math.floor(Math.abs(num));
      const mFull = (Math.abs(num) - d) * 60;
      const m = Math.floor(mFull);
      const s = Math.round((mFull - m) * 60);
      const sign = num < 0 ? '-' : '';
      return `${sign}${d}° ${m}′ ${s}″`;
    }
  }
  return str;
}

/**
 * Presentation component for showing target parameters in tree view categories.
 */
export const TargetDetails: React.FC<TargetDetailsProps> = ({
  catalogId = '',
  commonName = '',
  camera,
  telescope,
  expTime,
  filterGroups = [],
  ra = '',
  dec = '',
  averageValue,
  onCatalogIdChange,
  onCommonNameChange,
}) => (
  <div className="target-details">
    <div className="target-details__content">

      {/* Tree 1: Information */}
      <div className="target-details__tree-section">
        <div className="target-details__tree-header">Information</div>

        <div className="target-details__tree-row">
          <span className="tree-branch">└─</span>
          <label htmlFor="catalog-id" className="tree-label">Catalog ID:</label>
          {onCatalogIdChange ? (
            <input
              id="catalog-id"
              className="target-input"
              type="text"
              value={catalogId}
              onChange={(e) => onCatalogIdChange(e.target.value)}
            />
          ) : (
            <span className="tree-value">{catalogId || '—'}</span>
          )}
        </div>

        <div className="target-details__tree-row">
          <span className="tree-branch">└─</span>
          <label htmlFor="common-name" className="tree-label">Common Name:</label>
          {onCommonNameChange ? (
            <input
              id="common-name"
              className="target-input"
              type="text"
              value={commonName}
              onChange={(e) => onCommonNameChange(e.target.value)}
            />
          ) : (
            <span className="tree-value">{commonName || '—'}</span>
          )}
        </div>

        <div className="target-details__tree-row">
          <span className="tree-branch">└─</span>
          <span className="tree-label">Coordinates:</span>
          <span className="tree-value">
            {ra || dec ? `${formatRaString(ra)} • ${formatDecString(dec)}` : '—'}
          </span>
        </div>

        <div className="target-details__tree-row">
          <span className="tree-branch">└─</span>
          <span className="tree-label">Exposure Time:</span>
          <span className="tree-value">{expTime || '0s'}</span>
        </div>

        {averageValue && (
          <div className="target-details__tree-row">
            <span className="tree-branch">└─</span>
            <span className="tree-label">Average Value:</span>
            <span className="tree-value">{averageValue}</span>
          </div>
        )}
      </div>

      {/* Tree 2: Equipment */}
      <div className="target-details__tree-section">
        <div className="target-details__tree-header">Equipment</div>

        <div className="target-details__tree-row">
          <span className="tree-branch">└─</span>
          <span className="tree-label">Camera:</span>
          <span className="tree-value">{camera || 'ZWO ASI 533MM Pro'}</span>
        </div>

        <div className="target-details__tree-row">
          <span className="tree-branch">└─</span>
          <span className="tree-label">Telescope:</span>
          <span className="tree-value">{telescope || 'Apertura 75Q'}</span>
        </div>
      </div>

      {/* Tree 3: Frames */}
      <div className="target-details__tree-section target-details__tree-section--frames">
        <div className="target-details__tree-header">Frames</div>
        <div className="target-details__frames-list">
          {filterGroups && filterGroups.length > 0 ? (
            filterGroups.map((group) => (
              <div className="target-details__tree-group" key={group.filterName}>
                <div className="target-details__tree-row">
                  <span className="tree-branch">└─</span>
                  <span className="tree-label">{group.filterName}</span>
                </div>
                {group.exposures.map((exp) => (
                  <div className="target-details__tree-row target-details__tree-row--sub" key={exp.duration}>
                    <span className="tree-branch">└─</span>
                    <span className="tree-value">{exp.count} × {exp.duration}″</span>
                  </div>
                ))}
              </div>
            ))
          ) : (
            <div className="target-details__tree-row">
              <span className="tree-branch">└─</span>
              <span className="tree-value">—</span>
            </div>
          )}
        </div>
      </div>

    </div>
  </div>
);
