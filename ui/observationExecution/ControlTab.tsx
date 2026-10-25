/**
 * @fileoverview The "Control" tab: telescope, manual guide, guiding commands,
 * execution queue and test frame.
 *
 * Every control follows the command policy. While Ekos is in charge (or the
 * policy is still loading or failed) every control is disabled, the command
 * handlers refuse to run, and the reason is visible text linked to the
 * controls with `aria-describedby`.
 */

import React from 'react';
import { SectionPanel } from '../common/components/SectionPanel';
import { useTelescopeStatus } from '../common/hooks/useTelescopeStatus';
import { useTargetContext } from '../common/context/TargetContext';
import { navigateToElement } from '../common/utils/displayCoordinator';
import { OBSERVATION_PLANNER_MODE } from '../common/components/quality/useStatementActions';
import { useExecutionQueue } from './hooks/useExecutionQueue';
import type { SequenceObject } from './hooks/useExecutionQueue';
import { useEquipmentCommands } from './hooks/useEquipmentCommands';
import type { EquipmentCommandPolicy } from './hooks/useEquipmentCommandPolicy';
import { DirectionalControl } from '../observatoryManager/panels/DirectionalControl';
import { TelescopeControl } from '../observatoryManager/panels/TelescopeControl';
import { ExecutionQueue } from './ExecutionQueue';
import { TestFramePanel } from './TestFramePanel';

/** The id of the visible text that gives the reason the controls are off. */
export const CONTROL_REASON_ID = 'execution-control-reason';

export interface ControlTabProps {
    /** Whether the equipment may be commanded, and why not. */
    policy: EquipmentCommandPolicy;
}

/**
 * The control tab.
 * @param props The command policy.
 * @return The tab's content.
 */
export const ControlTab: React.FC<ControlTabProps> = ({ policy }) => {
    const disabled = !policy.canCommand;
    const { selectedTarget, raShared, decShared } = useTargetContext();
    const { trackingStatus, telemetry } = useTelescopeStatus();
    const { handlers, guidingExposure, guidingGain, setGuidingExposure, setGuidingGain } = useEquipmentCommands(
        policy.canCommand,
    );
    const { queue, removeFromQueue, reorderQueue } = useExecutionQueue();

    const editInPlanner = (sequence: SequenceObject) => {
        void navigateToElement({
            targetDisplay: OBSERVATION_PLANNER_MODE,
            targetElement: 'dataQuality',
            action: 'openInPlanner',
            payload: { targetId: sequence.target_name },
        });
    };

    return (
        <div className="control-tab">
            <p id={CONTROL_REASON_ID} className="control-tab__reason">
                {policy.reason}
            </p>
            <div className="control-tab__grid" aria-describedby={CONTROL_REASON_ID}>
                <SectionPanel title="Telescope">
                    <TelescopeControl
                        isTracking={trackingStatus === 'Tracking'}
                        isParked={trackingStatus === 'Parked'}
                        onSetTracking={(enabled) => void handlers.onSetTracking(enabled)}
                        onPark={() => void handlers.onPark()}
                        onUnpark={() => void handlers.onUnpark()}
                        onStartAlignment={() => void handlers.onStartAlignment(raShared, decShared)}
                        onStopAlignment={() => void handlers.onStopAlignment()}
                        isAligningActive={telemetry.alignmentActive}
                        disabled={disabled}
                    />
                    <button
                        type="button"
                        className="btn btn--primary"
                        disabled={disabled}
                        aria-disabled={disabled}
                        aria-describedby={CONTROL_REASON_ID}
                        onClick={() => void handlers.onSlewToTarget(raShared, decShared)}
                    >
                        Slew to Target
                    </button>
                </SectionPanel>
                <SectionPanel title="Manual Guide">
                    <DirectionalControl
                        onStartMove={(direction) => void handlers.onStartMove(direction)}
                        onStopMove={(direction) => void handlers.onStopMove(direction)}
                        onSetSlewRate={(rate) => void handlers.onSetSlewRate(rate)}
                        onStop={() => void handlers.onStop()}
                        disabled={disabled}
                    />
                </SectionPanel>
                <SectionPanel title="Guiding Commands">
                    <div className="control-tab__guide">
                        <label>
                            Guide exposure (s)
                            <input
                                type="number"
                                min={0.1}
                                step="any"
                                value={guidingExposure}
                                disabled={disabled}
                                aria-disabled={disabled}
                                onChange={(event) => setGuidingExposure(Number(event.target.value))}
                            />
                        </label>
                        <label>
                            Guide gain
                            <input
                                type="number"
                                min={0}
                                value={guidingGain}
                                disabled={disabled}
                                aria-disabled={disabled}
                                onChange={(event) => setGuidingGain(Number(event.target.value))}
                            />
                        </label>
                        <button type="button" className="btn" disabled={disabled} aria-disabled={disabled} aria-describedby={CONTROL_REASON_ID} onClick={() => void handlers.onGuideStart()}>
                            Start guiding
                        </button>
                        <button type="button" className="btn" disabled={disabled} aria-disabled={disabled} aria-describedby={CONTROL_REASON_ID} onClick={() => void handlers.onGuideStop()}>
                            Stop guiding
                        </button>
                        <button type="button" className="btn" disabled={disabled} aria-disabled={disabled} aria-describedby={CONTROL_REASON_ID} onClick={() => void handlers.onGuideCapture()}>
                            Capture guide frame
                        </button>
                    </div>
                </SectionPanel>
                <SectionPanel title="Test Frame">
                    <TestFramePanel targetId={selectedTarget} disabled={disabled} disabledReasonId={CONTROL_REASON_ID} />
                </SectionPanel>
            </div>
            <fieldset className="control-tab__queue" disabled={disabled} aria-disabled={disabled}>
                <legend className="visually-hidden">Execution queue</legend>
                <ExecutionQueue
                    queue={queue}
                    removeFromQueue={removeFromQueue}
                    onEdit={editInPlanner}
                    reorderQueue={reorderQueue}
                    commandsDisabled={disabled}
                    disabledReasonId={CONTROL_REASON_ID}
                />
            </fieldset>
        </div>
    );
};
