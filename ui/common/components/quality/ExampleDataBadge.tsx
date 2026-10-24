/**
 * @fileoverview The badge that marks a panel showing example values.
 *
 * A panel that shows a value from `ui/common/examples/` carries this badge
 * and sets `data-example="true"` on its own root. Wave L removes both.
 */

import React from 'react';

/**
 * Shows an icon and the words "Example data".
 * @return The badge.
 */
export const ExampleDataBadge: React.FC = () => (
    <span className="example-data-badge" data-testid="example-data-badge">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true" focusable="false">
            <path d="M5 3 H15 L19 7 V21 H5 Z" />
            <path d="M9 12 H15 M9 16 H15" />
        </svg>
        Example data
    </span>
);
