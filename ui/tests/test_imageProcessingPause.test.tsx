/**
 * @fileoverview Tests that Image Processing keeps its state when it is hidden and
 * shown again. State that is cleared "when the target changes" must be cleared only
 * on a real change, and a hand-off sent while the display is hidden arrives on show.
 */

import React, { Activity, useState } from 'react';
import { beforeEach, describe, expect, it } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import { useOnValueChange } from '../imageProcessingDisplay/hooks/useOnValueChange';
import { postHandoff, useHandoffConsumer, resetHandoffsForTests } from '../common/utils/displayHandoffStore';

/** Holds a picked frame that is cleared only when the target changes. */
const Probe: React.FC<{ target: string }> = ({ target }) => {
  const [frame, setFrame] = useState<string | null>(null);
  const [received, setReceived] = useState<string[]>([]);
  useOnValueChange(target, () => setFrame(null));
  useHandoffConsumer<string>('Image Processing', 'targetSelected', (payload) =>
    setReceived((previous) => [...previous, payload])
  );
  return (
    <div>
      <button onClick={() => setFrame('frame-7')}>pick</button>
      <span data-testid="frame">{frame ?? 'none'}</span>
      <span data-testid="received">{received.join(',')}</span>
    </div>
  );
};

/** Shows or hides the probe the way App.tsx pauses a display. */
const Shell: React.FC<{ target: string; visible: boolean }> = ({ target, visible }) => (
  <Activity mode={visible ? 'visible' : 'hidden'}>
    <Probe target={target} />
  </Activity>
);

describe('Image Processing pause and show', () => {
  beforeEach(() => {
    resetHandoffsForTests();
  });

  /** The picked frame survives a hide and show of the same target, and is cleared by a new target. */
  it('keeps the selected frame across hide and show, and clears it on a target change', () => {
    const { rerender } = render(<Shell target="M 31" visible />);
    act(() => screen.getByText('pick').click());
    expect(screen.getByTestId('frame').textContent).toBe('frame-7');

    rerender(<Shell target="M 31" visible={false} />);
    rerender(<Shell target="M 31" visible />);
    expect(screen.getByTestId('frame').textContent).toBe('frame-7');

    rerender(<Shell target="M 81" visible />);
    expect(screen.getByTestId('frame').textContent).toBe('none');
  });

  /** A hand-off posted while the display is hidden is delivered once, when it is shown. */
  it('delivers a hand-off posted while hidden when the display is shown', () => {
    const { rerender } = render(<Shell target="M 31" visible={false} />);
    act(() => {
      postHandoff('Image Processing', 'targetSelected', 'M 42');
    });
    expect(screen.getByTestId('received').textContent).toBe('');

    rerender(<Shell target="M 31" visible />);
    expect(screen.getByTestId('received').textContent).toBe('M 42');
  });
});
