/**
 * @fileoverview The actions a quality statement can offer: Evidence and Show
 * in calibration library.
 *
 * Both ask another display to open (through `navigateToElement`). The old
 * "Send to plan" action is retired.
 */

import { useCallback } from 'react';
import { navigateToElement } from '../../utils/displayCoordinator';
import type { TargetStatement } from '../../types/backendTypes';

/** The controller a statement uses to show and run its actions. */
export interface StatementActions {
    /** Opens the cited frames in the Image Viewer. */
    showFrames: (rowIds: number[], statementText: string) => void;
    /** Opens a calibration row in the Observatory Manager. */
    showCalibrationRow: (statement: TargetStatement) => void;
}

/**
 * Builds the actions for the statements of one target.
 * @param targetId The target the statements are about.
 * @return The action controller.
 */
export function useStatementActions(targetId: string): StatementActions {
    const showFrames = useCallback(
        (rowIds: number[], statementText: string) => {
            void navigateToElement({
                targetDisplay: 'Image Viewer',
                targetElement: 'frameList',
                action: 'showFrames',
                payload: { rowIds, targetId, statementText },
            });
        },
        [targetId],
    );

    const showCalibrationRow = useCallback((statement: TargetStatement) => {
        void navigateToElement({
            targetDisplay: 'Observatory Manager',
            targetElement: 'calibrationLibrary',
            action: 'showCalibrationRow',
            payload: {
                calibrationKey: statement.proposedAction?.calibrationFrameType ?? null,
                captureSetting: statement.captureSetting ?? null,
                rule: statement.rule,
            },
            focus: { scrollToElement: true, flashHighlight: true },
        });
    }, []);

    return { showFrames, showCalibrationRow };
}
