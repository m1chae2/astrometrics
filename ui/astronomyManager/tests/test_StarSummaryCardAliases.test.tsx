/**
 * @file test_StarSummaryCardAliases.test.tsx
 * @description Tests that the star card lists the star's other names: a few shown, the rest behind "+N more".
 */
import React from 'react';
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { StarSummaryCard } from '../components/StarSummaryCard';
import { describeAliases } from '../../common/utils/starDisplayFormat';

describe('describeAliases', () => {
  it('drops the star\'s own name, repeats and the NAME prefix, and keeps the given order', () => {
    const result = describeAliases(['NAME Vega', 'vega', '* alf Lyr', 'HD 172167', 'HIP 91262', 'Gaia DR3 2'], 'Vega');
    expect(result.shown).toEqual(['* alf Lyr', 'HD 172167', 'HIP 91262']);
    expect(result.hidden).toEqual(['Gaia DR3 2']);
  });

  it('gives nothing when there are no aliases', () => {
    expect(describeAliases(undefined, 'Vega')).toEqual({ shown: [], hidden: [] });
  });
});

describe('StarSummaryCard aliases', () => {
  it('shows three names and "+N more" with the rest in the tooltip', () => {
    render(<StarSummaryCard starId="Vega" astronomyData={{ id: 'Vega', name: 'Vega', aliases: ['alf Lyr', 'HD 172167', 'HIP 91262', 'HR 7001', 'Gaia DR3 1'] }} />);
    expect(screen.getByTestId('star-aliases').textContent).toContain('alf Lyr, HD 172167, HIP 91262');
    const more = screen.getByTestId('star-aliases-more');
    expect(more.textContent).toBe('+2 more');
    expect(more.getAttribute('title')).toBe('HR 7001, Gaia DR3 1');
  });

  it('shows no line when the star has no other names', () => {
    render(<StarSummaryCard starId="Vega" astronomyData={{ id: 'Vega', name: 'Vega' }} />);
    expect(screen.queryByTestId('star-aliases')).toBeNull();
  });
});
