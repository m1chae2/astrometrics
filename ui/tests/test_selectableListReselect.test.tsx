/**
 * @fileoverview Tests that picking a row in the shared selectable list always
 * reports the pick, including when that row is already the highlighted one.
 * The Planetarium relies on this: its star list highlights the first star
 * before anything was chosen, and clicking it must still select and slew.
 */

import { describe, it, expect, vi, beforeAll } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { SelectableList } from '../common/components/SelectableList';

const items = [
  { id: 'a', value: 'a', label: 'Alpha' },
  { id: 'b', value: 'b', label: 'Beta' },
];

describe('SelectableList picks', () => {
  beforeAll(() => {
    // jsdom has no ResizeObserver, which the list uses to measure its height.
    vi.stubGlobal('ResizeObserver', class {
      observe(): void {}
      disconnect(): void {}
    });
  });

  it('reports a pick on a row that is not yet selected', () => {
    const onSelect = vi.fn();
    render(<SelectableList items={items} selectedId="a" pendingId="a" onSelect={onSelect} />);

    fireEvent.click(screen.getByText('Beta'));

    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith('b');
  });

  it('reports a pick on the row that is already highlighted', () => {
    const onSelect = vi.fn();
    render(<SelectableList items={items} selectedId="a" pendingId="a" onSelect={onSelect} />);

    fireEvent.click(screen.getByText('Alpha'));

    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect).toHaveBeenCalledWith('a');
  });
});
