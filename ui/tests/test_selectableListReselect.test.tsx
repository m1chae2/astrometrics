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

  it('reports a pick to its own list when another list with the same class name is mounted', () => {
    // A hidden display stays mounted beside the visible one, and both use the same class name.
    // Clicking a row's label must reach the row's own radio, not the same-numbered row of the
    // other list.
    const onSelectHidden = vi.fn();
    const onSelectVisible = vi.fn();
    render(
      <>
        <SelectableList className="shared-list" items={items} onSelect={onSelectHidden} />
        <SelectableList className="shared-list" items={items} onSelect={onSelectVisible} />
      </>,
    );

    fireEvent.click(screen.getAllByText('Beta')[1]);

    expect(onSelectVisible).toHaveBeenCalledWith('b');
    expect(onSelectHidden).not.toHaveBeenCalled();
  });

  it('gives each list its own radio ids and group name', () => {
    const { container } = render(
      <>
        <SelectableList className="shared-list" items={items} onSelect={vi.fn()} />
        <SelectableList className="shared-list" items={items} onSelect={vi.fn()} />
      </>,
    );
    const inputs = Array.from(container.querySelectorAll('input[type="radio"]'));
    expect(new Set(inputs.map((input) => input.id)).size).toBe(inputs.length);
    expect(new Set(inputs.map((input) => (input as HTMLInputElement).name)).size).toBe(2);
  });
});
