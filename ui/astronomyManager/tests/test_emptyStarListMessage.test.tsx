/**
 * @fileoverview Checks that a target with no stars shows the star list's
 * empty message, which is the one place an empty display is correct.
 */

import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { RadioListManager } from '../../common/radioList/RadioListManager';

vi.stubGlobal('ResizeObserver', class {
  observe() {}
  disconnect() {}
  unobserve() {}
});

describe('Star list for a target with no stars', () => {
  /** The list says there are no stars instead of staying blank. */
  it('shows the empty message', () => {
    render(
      <RadioListManager
        title="Stars in Empty"
        items={[]}
        selectedId=""
        pendingId=""
        onSelect={vi.fn()}
        filterText=""
        onFilterTextChange={vi.fn()}
        onFilterOptionChange={vi.fn()}
        emptyMessage="No stars found for this selection."
      />
    );
    expect(screen.getByText('No stars found for this selection.')).toBeTruthy();
  });
});
