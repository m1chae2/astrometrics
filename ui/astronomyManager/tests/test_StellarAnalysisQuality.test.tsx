/**
 * @file test_StellarAnalysisQuality.test.tsx
 * @description Unit tests for the Quality card in the Stellar Analysis panel.
 * Verifies that it asks about the star under the right target and shows the first statement.
 */

import React from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render as baseRender, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StellarAnalysisDetails } from '../components/StellarAnalysisDetails';
import { TargetProvider } from '../../common/context/TargetContext';
import { Confidence, Severity, StatementKind } from '../../common/types/backendTypes';

const callBackendMock = vi.fn();

vi.mock('../../common/services/backendApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../common/services/backendApi')>()),
  callBackend: (...args: unknown[]) => callBackendMock(...args),
}));

/**
 * Renders inside a fresh query client and target provider, as the app does.
 * @param ui The element to render.
 * @returns The render result.
 */
function render(ui: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return baseRender(
    <QueryClientProvider client={client}>
      <TargetProvider>{ui}</TargetProvider>
    </QueryClientProvider>
  );
}

describe('StellarAnalysisDetails Quality card', () => {
  beforeEach(() => {
    callBackendMock.mockReset();
    callBackendMock.mockResolvedValue([]);
    window.localStorage.clear();
  });

  /**
   * Verifies the card asks about this star under its first target and shows the first statement.
   */
  it('shows the statements for the star under its first target', async () => {
    callBackendMock.mockResolvedValue([
      {
        rule: 'rule_a',
        kind: StatementKind.WARNING,
        severity: Severity.WARNING,
        message: 'Spectrum is noisy.',
        confidence: Confidence.MEASURED,
      },
    ]);
    render(<StellarAnalysisDetails astronomyData={{ id: 'Vega', name: 'Vega', targetIds: ['M 13', 'NGC 6205'] }} />);

    await waitFor(() => expect(screen.getByText('Spectrum is noisy.')).toBeInTheDocument());
    expect(screen.getByText('Quality')).toBeInTheDocument();
    expect(callBackendMock).toHaveBeenCalledWith('advisory:result', { target: 'M 13', object_id: 'Vega' });
  });

  /**
   * Verifies the card shows the plain sentence of a result and not the technical message.
   */
  it('shows the plain message of a result', async () => {
    callBackendMock.mockResolvedValue([
      {
        rule: 'classification',
        kind: StatementKind.RESULT,
        message: 'Teff 4500 K from B-V 1.2.',
        plainMessage: 'This star is a cool orange giant.',
        confidence: Confidence.MEASURED,
      },
    ]);
    render(<StellarAnalysisDetails astronomyData={{ id: 'Vega', name: 'Vega', targetIds: ['M 13'] }} />);

    await waitFor(() => expect(screen.getByText('This star is a cool orange giant.')).toBeInTheDocument());
    expect(screen.getByText('Result')).toBeInTheDocument();
    expect(screen.queryByText(/Teff 4500/)).toBeNull();
  });

  /**
   * Verifies a star with no target shows no Quality card and asks nothing.
   */
  it('shows no Quality card and does not fetch when the star has no target', () => {
    render(<StellarAnalysisDetails astronomyData={{ id: 'Vega', name: 'Vega' }} />);
    expect(callBackendMock).not.toHaveBeenCalled();
    expect(document.querySelector('.quality-chip')).toBeNull();
  });
});
