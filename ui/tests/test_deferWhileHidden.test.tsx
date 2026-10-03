/**
 * @fileoverview Tests that a hidden display does not react to target changes.
 * A hidden display keeps seeing the target it had when it was last on screen,
 * and catches up when it is shown again, so selecting a target does not make
 * every display fetch that target's data.
 */

import { describe, it, expect } from 'vitest';
import { useEffect } from 'react';
import { render, screen, act } from '@testing-library/react';
import { TargetProvider, DeferWhileHidden, useTargetContext } from '../common/context/TargetContext';

/** Shows the selected target; exposes the setter through a button-less handle. */
function Probe({ label }: { label: string }): React.ReactElement {
  const { selectedTarget, setSelectedTarget } = useTargetContext();
  useEffect(() => {
    (window as any)[`set_${label}`] = setSelectedTarget;
  }, [label, setSelectedTarget]);
  return <span data-testid={label}>{selectedTarget}</span>;
}

describe('DeferWhileHidden', () => {
  it('holds the old target while hidden and catches up when shown', () => {
    window.localStorage.setItem('selectedTarget', 'M_1');
    const tree = (hiddenActive: boolean) => (
      <TargetProvider>
        <DeferWhileHidden active>
          <Probe label="visible" />
        </DeferWhileHidden>
        <DeferWhileHidden active={hiddenActive}>
          <Probe label="other" />
        </DeferWhileHidden>
      </TargetProvider>
    );
    const view = render(tree(true));
    expect(screen.getByTestId('other').textContent).toBe('M_1');

    view.rerender(tree(false));
    act(() => (window as any).set_visible('M_2'));
    expect(screen.getByTestId('visible').textContent).toBe('M_2');
    expect(screen.getByTestId('other').textContent).toBe('M_1');

    view.rerender(tree(true));
    expect(screen.getByTestId('other').textContent).toBe('M_2');
  });
});
