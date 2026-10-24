/**
 * @file useCommandAvailability.ts
 * @description Tells the Observatory Manager whether it may command a device.
 * The app never commands a device while Ekos captures. Until the library
 * returns, per capability, whether this app may command it, the answer is
 * always "no" (fail safe), and the reason is shown as visible text. The hook
 * also reads the delegation state of each capability (`observatory:safety_status`)
 * so the screen can show who is in control.
 */

import { useQuery } from '@tanstack/react-query';
import { callBackend } from '../../common/services/backendApi';
import type { CapabilityDelegation } from '../../common/types/backendTypes';

/** The text shown while the policy is loading or failed to load. */
export const CHECKING_CONTROL_TEXT = 'Checking whether Ekos is in control…';

/** The text shown once the policy is known. Commands stay off until the library answers per capability. */
export const COMMANDS_OFF_TEXT =
    'Equipment commands are off. Ekos controls the equipment; this app only watches. Commands return when the library reports which capabilities this app may command.';

/** What {@link useCommandAvailability} returns. */
export interface CommandAvailability {
    /** True only when this app may command the device. Always false until the library reports it. */
    canCommand: boolean;
    /** The visible reason commands are off. */
    reason: string;
    /** The delegation state of each capability, in the order returned, or empty while unknown. */
    delegations: CapabilityDelegation[];
    /** Whether the policy is still loading. */
    isChecking: boolean;
}

/**
 * Reads the delegation policy and gives the fail-safe command answer.
 * @return The availability.
 */
export function useCommandAvailability(): CommandAvailability {
    const query = useQuery({
        queryKey: ['observatory', 'safetyStatus', 'delegationPolicy'],
        queryFn: () => callBackend('observatory:safety_status', {}),
        retry: false,
    });
    const isChecking = query.isPending || query.isError;
    return {
        // Wave L adds a per-capability "can command" value; until then nothing is commandable.
        canCommand: false,
        reason: isChecking ? CHECKING_CONTROL_TEXT : COMMANDS_OFF_TEXT,
        delegations: query.data?.delegation_policy?.capability_delegations ?? [],
        isChecking: query.isPending,
    };
}
