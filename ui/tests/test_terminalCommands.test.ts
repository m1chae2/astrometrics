/**
 * @file test_terminalCommands.test.ts
 * @description Unit tests for the Command Console terminal's own commands (`clear`,
 * `clear('workspace')`, `doc name`), the navigation request a `show(...)` call becomes, and the
 * elapsed-time text of the running line.
 */
import { describe, it, expect } from 'vitest';
import { formatElapsed, parseTerminalCommand, showRequestIntent } from '../commandConsole/terminalCommands';

describe('parseTerminalCommand', () => {
  /** Only the exact bare lines are terminal commands; anything else is Python. */
  it('reads clear in both forms and leaves other lines to Python', () => {
    expect(parseTerminalCommand('clear')).toEqual({ kind: 'clear-terminal' });
    expect(parseTerminalCommand('  clear()  ')).toEqual({ kind: 'clear-terminal' });
    expect(parseTerminalCommand("clear('workspace')")).toEqual({ kind: 'clear-workspace' });
    expect(parseTerminalCommand('clear( "workspace" )')).toEqual({ kind: 'clear-workspace' });
    expect(parseTerminalCommand("clear('everything')")).toEqual({ kind: 'clear-usage' });
    expect(parseTerminalCommand('clear = 5')).toEqual({ kind: 'python', code: 'clear = 5' });
    expect(parseTerminalCommand('clear.cache')).toEqual({ kind: 'python', code: 'clear.cache' });
    expect(parseTerminalCommand("clear('workspace\")")).toEqual({ kind: 'clear-usage' });
  });

  /** `doc name` and `doc(name)` become a call with the name as text; a quoted call is unchanged. */
  it('turns doc name into a Python call', () => {
    expect(parseTerminalCommand('doc delete_images')).toEqual({ kind: 'python', code: 'doc("delete_images")' });
    expect(parseTerminalCommand('doc(astrometrics.targets.delete_images)'))
      .toEqual({ kind: 'python', code: 'doc("astrometrics.targets.delete_images")' });
    expect(parseTerminalCommand("doc('TargetCatalog.delete_images')"))
      .toEqual({ kind: 'python', code: "doc('TargetCatalog.delete_images')" });
    expect(parseTerminalCommand('doc = 3')).toEqual({ kind: 'python', code: 'doc = 3' });
  });
});

describe('showRequestIntent', () => {
  const base = { kind: 'show' as const, target: 'M 31', star_id: null, star_name: null, path: null };

  /** A target, a target with a star, and a target with a file each use Image Processing's own hand-off. */
  it('builds the Image Processing hand-offs', () => {
    expect(showRequestIntent({ ...base, display: 'Image Processing' })).toMatchObject({
      targetDisplay: 'Image Processing', action: 'targetSelected', payload: { targetId: 'M 31' },
    });
    expect(showRequestIntent({ ...base, display: 'Image Processing', star_id: 'vega', star_name: 'Vega' }).payload).toEqual({
      targetId: 'M 31', enableAstrometry: true, focusLibraryStarId: 'vega', focusStarName: 'Vega',
    });
    expect(showRequestIntent({ ...base, display: 'Image Processing', path: '/s.fits' }).payload)
      .toEqual({ targetId: 'M 31', filePath: '/s.fits' });
  });

  /** A star alone opens in the Astronomy Manager. */
  it('builds the Astronomy Manager hand-off', () => {
    expect(showRequestIntent({ ...base, display: 'Astronomy Manager', target: null, star_id: 'vega', star_name: 'Vega' }))
      .toMatchObject({ targetDisplay: 'Astronomy Manager', action: 'astronomySelectStar', payload: { starId: 'vega' } });
  });
});

describe('formatElapsed', () => {
  /** Minutes and two-digit seconds. */
  it('writes minutes and seconds', () => {
    expect(formatElapsed(0)).toBe('0:00');
    expect(formatElapsed(7_900)).toBe('0:07');
    expect(formatElapsed(750_000)).toBe('12:30');
  });
});
