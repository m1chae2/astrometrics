/**
 * @fileoverview Tells Observation Execution whether this app may command the
 * equipment.
 *
 * Decision S10: until wave L3 returns a "can command" value for each
 * capability, no control is enabled. The hook still reads the safety status,
 * so the visible reason can say whether the check is running ("Checking
 * whether Ekos is in control…") or has finished. It fails safe: while the
 * read is loading, or if it fails, nothing can be commanded.
 */

import { useQuery } from '@tanstack/react-query';
import { callBackend } from '../../common/services/backendApi';

/** Whether the library returns a "can command" value for each capability yet. Wave L3 sets this to true. */
export const PER_CAPABILITY_COMMAND_STATE_AVAILABLE = false;

/** The React Query key of the safety status read used for the policy. */
export const COMMAND_POLICY_QUERY_KEY = ['observatory', 'safety-status', 'delegation-policy'] as const;

/** What the controls need to know. */
export interface EquipmentCommandPolicy {
    /** True when the equipment may be commanded. */
    canCommand: boolean;
    /** The reason shown as visible text beside the disabled controls. */
    reason: string;
    /** True while the policy is still loading. */
    isChecking: boolean;
}

/**
 * Reads the command policy.
 * @return The policy, which is "cannot command" in every case today.
 */
export function useEquipmentCommandPolicy(): EquipmentCommandPolicy {
    const status = useQuery({
        queryKey: COMMAND_POLICY_QUERY_KEY,
        queryFn: () => callBackend('observatory:safety_status', {}),
    });
    if (status.isLoading) {
        return { canCommand: false, isChecking: true, reason: 'Checking whether Ekos is in control…' };
    }
    if (status.isError) {
        return {
            canCommand: false,
            isChecking: false,
            reason: 'Could not check whether Ekos is in control, so the controls stay off.',
        };
    }
    return {
        canCommand: PER_CAPABILITY_COMMAND_STATE_AVAILABLE,
        isChecking: false,
        reason: 'Ekos is controlling the equipment. This display only watches.',
    };
}
