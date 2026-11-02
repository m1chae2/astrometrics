/**
 * @file test_lastImageViewerLayout.test.ts
 * @description Tests the Last Image panel layout rules in observationManager.css. The panel's
 * content area must be a flex column (so the image box fills it), and the FITS renderer must
 * fill its frame (its only content is an absolutely positioned canvas, so it would otherwise
 * shrink to nothing and the image would be drawn at scale 0).
 */

import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const css = readFileSync('ui/observationManager/observationManager.css', 'utf8');

/**
 * Returns the declaration block of the first rule with exactly this selector.
 *
 * @param selector The CSS selector text.
 * @return The text between the braces, or an empty string when the rule is missing.
 */
function ruleBody(selector: string): string {
    const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const match = new RegExp(`(?:^|\\n)${escaped}\\s*\\{([^}]*)\\}`).exec(css);
    return match ? match[1] : '';
}

describe('Last Image panel layout', () => {
    /**
     * The panel content class is panel__content; a rule for another class name never matches.
     */
    it('makes the panel content area a flex column', () => {
        const body = ruleBody('.last-image-viewer .panel__content');
        expect(body).toMatch(/display:\s*flex/);
        expect(body).toMatch(/flex-direction:\s*column/);
        expect(css).not.toContain('.section-panel__content');
    });

    /**
     * The renderer must grow to fill the frame so that its canvas container has a real size.
     */
    it('makes the FITS renderer fill its frame', () => {
        const body = ruleBody('.last-image-viewer__frame .fits-renderer');
        expect(body).toMatch(/flex:\s*1/);
    });
});
