/**
 * @file useCommandAvailability.ts
 * @description Tells the Observatory Manager whether it may command a device.
 * The app never commands a device while Ekos captures. Until the library
 * returns, per capability, whether this app may command it, the answer is
 * "no" (fail safe), and the reason is shown as visible text. Commands are on
 * only when the delegation policy lists the mount as AUTHORITATIVE (the app has
 * taken control, as it does in the simulator). The hook
 * also reads the delegation state of each capability (`observatory:safety_status`)
 * so the screen can show who is in control.
 */

import { useQuery } from '@tanstack/react-query';
import { callBackend } from '../services/backendApi';
import { describeCommandsStillOff, isNotLiveLaunch } from '../services/observatoryService';
import { isObservatorySourceSettled, useObservatorySourceQuery } from '../queries/useObservatorySourceQuery';
import { DelegationState, ObservatoryCapability, type CapabilityDelegation } from '../types/backendTypes';

/** The text shown while the policy is loading or failed to load. */
export const CHECKING_CONTROL_TEXT = 'Checking whether Ekos is in control…';

/** The text shown once the policy is known. Commands stay off until the library answers per capability. */
export const COMMANDS_OFF_TEXT =
    'Equipment commands are off. Ekos controls the equipment; this app only watches. Commands return when the library reports which capabilities this app may command.';

/** The text shown, when commands are on, to say where they go. */
export const SIMULATOR_COMMANDS_TEXT = 'Simulator: commands go to the simulated devices on this computer.';

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
    /**
     * True only when this app may command the given capability: that capability's own delegation state is
     * AUTHORITATIVE and the source is settled and not failed.
     */
    canCommandCapability: (capability: ObservatoryCapability) => boolean;
    /** The visible reason the given capability is off, or an empty string while it is on. */
    reasonForCapability: (capability: ObservatoryCapability) => string;
}

/** Plain names of the capabilities, used in the reason text. */
const CAPABILITY_NAMES: Record<ObservatoryCapability, string> = {
    [ObservatoryCapability.MOUNT_CONTROL]: 'the mount',
    [ObservatoryCapability.PLATE_SOLVE_ALIGNMENT]: 'alignment',
    [ObservatoryCapability.AUTOGUIDING]: 'guiding',
    [ObservatoryCapability.AUTOFOCUS]: 'the focuser',
    [ObservatoryCapability.CAPTURE_ORCHESTRATION]: 'the camera',
    [ObservatoryCapability.OBSERVATORY_SAFETY]: 'the safety checks',
};

/**
 * Reads the delegation policy and gives the fail-safe command answer.
 * @return The availability.
 */
export function useCommandAvailability(): CommandAvailability {
    const source = useObservatorySourceQuery();
    const sourceSettled = isObservatorySourceSettled(source);
    // The policy is asked once more when the source settles, because the simulator takes control while it starts.
    const query = useQuery({
        queryKey: ['observatory', 'safetyStatus', 'delegationPolicy', sourceSettled],
        queryFn: () => callBackend('observatory:safety_status', {}),
        retry: false,
    });
    const delegations = query.data?.delegation_policy?.capability_delegations ?? [];
    const canCommandCapability = (capability: ObservatoryCapability): boolean => delegations.some(
        (entry) => entry.capability === capability && entry.state === DelegationState.AUTHORITATIVE,
    ) && sourceSettled && !source?.failed;
    const canCommand = canCommandCapability(ObservatoryCapability.MOUNT_CONTROL);
    // A world that is not on live hardware is up, but the app may not have command of it yet.
    const simulatorUp = isNotLiveLaunch(source) && sourceSettled && !source?.failed;
    const isChecking = query.isPending || query.isError;
    const reasonForCapability = (capability: ObservatoryCapability): string => {
        if (canCommandCapability(capability)) return '';
        if (simulatorUp) {
            return `The simulator is up, but the app has no command of ${CAPABILITY_NAMES[capability]} yet. ${source?.commands?.error ?? describeCommandsStillOff(source)}`.trim();
        }
        if (isChecking) return CHECKING_CONTROL_TEXT;
        return `Equipment commands are off for ${CAPABILITY_NAMES[capability]}. Ekos controls it; this app only watches.`;
    };
    return {
        canCommandCapability,
        reasonForCapability,
        canCommand,
        reason: canCommand
            ? (simulatorUp ? `${SIMULATOR_COMMANDS_TEXT} ${describeCommandsStillOff(source)}`.trim() : '')
            : simulatorUp
                ? `The simulator is up, but the app has no command of it yet. ${source?.commands?.error ?? describeCommandsStillOff(source)}`.trim()
                : isChecking ? CHECKING_CONTROL_TEXT : COMMANDS_OFF_TEXT,
        delegations,
        isChecking: query.isPending,
    };
}
