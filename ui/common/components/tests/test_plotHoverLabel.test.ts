/**
 * @file test_plotHoverLabel.test.ts
 * @description Tests that the hover label of every plot is built from the theme tokens, not from
 * Plotly's default orange box.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { themedHoverLabel } from '../../utils/plotHoverLabel';
import { buildLayout } from '../../utils/plotTools';

const TOKENS = ['--surface-menu', '--border-subtle', '--plot-font', '--font-family-base', '--small-font-size'];

describe('themedHoverLabel', () => {
  afterEach(() => {
    TOKENS.forEach((token) => document.documentElement.style.removeProperty(token));
  });

  it('takes the box, edge, text color, font and size from the theme tokens', () => {
    const root = document.documentElement.style;
    root.setProperty('--surface-menu', 'rgb(46, 46, 50)');
    root.setProperty('--border-subtle', 'rgba(255, 255, 255, 0.08)');
    root.setProperty('--plot-font', 'rgb(255, 255, 255)');
    root.setProperty('--font-family-base', 'Ubuntu');
    root.setProperty('--small-font-size', '11px');
    expect(themedHoverLabel()).toEqual({
      bgcolor: 'rgb(46, 46, 50)',
      bordercolor: 'rgba(255, 255, 255, 0.08)',
      font: { color: 'rgb(255, 255, 255)', family: 'Ubuntu', size: 11 },
    });
  });

  it('is part of the shared layout builder', () => {
    expect(buildLayout().hoverlabel?.font?.size).toBe(11);
  });
});
