/**
 * @module useSendFramingToPlan
 * @fileoverview Lists the saved night plans and writes a framing into one of
 * them with `plan:edit`. Other windows refresh through `plan:changed`.
 */

import { useCallback, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { callBackend, ObservationSessionSummary } from '../../common/services/backendApi';
import { BackendError } from '../../common/services/backendError';
import { planQueryKeys } from '../../common/queries/qualityQueryKeys';
import type { MosaicPanel } from '../../common/types/backendTypes';

/** What happened to the last "Send to plan". */
export type SendFramingOutcome =
  | { kind: 'sending' }
  | { kind: 'sent'; planId: string }
  | { kind: 'conflict' }
  | { kind: 'rejected'; reason: string };

/** The framing the panel sends. */
export interface FramingToSend {
  /** Center right ascension in degrees. */
  centerRaDeg: number;
  /** Center declination in degrees. */
  centerDecDeg: number;
  /** Camera rotation in degrees east of north. */
  rotationDeg: number;
  /** The panels exactly as the library returned them. */
  panels: MosaicPanel[];
}

/**
 * Provides the plan list and the Send to plan action.
 *
 * @returns The plans, their loading and error state, the last outcome, and the actions.
 */
export function useSendFramingToPlan() {
  const queryClient = useQueryClient();
  const [outcome, setOutcome] = useState<SendFramingOutcome | null>(null);

  const plansQuery = useQuery<ObservationSessionSummary[]>({
    queryKey: planQueryKeys.list(),
    queryFn: async () => {
      const reply = await callBackend('plan:get', {});
      return Array.isArray(reply) ? reply : [];
    },
  });

  const sendToPlan = useCallback(
    async (planId: string, framing: FramingToSend) => {
      setOutcome({ kind: 'sending' });
      try {
        const session = await callBackend('plan:get', { session_id: planId });
        const revision = Array.isArray(session) ? undefined : session.revision;
        await callBackend('plan:edit', {
          session_id: planId,
          framing: { ...framing },
          expected_revision: revision,
        });
        void queryClient.invalidateQueries({ queryKey: planQueryKeys.list() });
        void queryClient.invalidateQueries({ queryKey: planQueryKeys.session(planId) });
        setOutcome({ kind: 'sent', planId });
      } catch (error) {
        if (error instanceof BackendError && error.code === 'conflict') {
          setOutcome({ kind: 'conflict' });
        } else {
          const reason = error instanceof Error ? error.message : 'The plan did not accept the framing.';
          setOutcome({ kind: 'rejected', reason });
        }
      }
    },
    [queryClient],
  );

  const createPlan = useCallback(async (): Promise<string | null> => {
    try {
      const created = await callBackend('plan:create', {});
      void queryClient.invalidateQueries({ queryKey: planQueryKeys.list() });
      return created.id;
    } catch (error) {
      const reason = error instanceof Error ? error.message : 'The plan could not be created.';
      setOutcome({ kind: 'rejected', reason });
      return null;
    }
  }, [queryClient]);

  const dismissOutcome = useCallback(() => setOutcome(null), []);

  return { plansQuery, outcome, sendToPlan, createPlan, dismissOutcome };
}
