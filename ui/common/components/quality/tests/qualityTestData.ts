/**
 * @fileoverview Builders of typed quality statements and reports for the tests.
 */

import {
    ActionClass,
    Confidence,
    ForecastOutcome,
    ScienceGoal,
    Severity,
    StatementKind,
} from '../../../types/backendTypes';
import type { TargetDatasetReport, TargetStatement } from '../../../types/backendTypes';

/**
 * Builds a statement with sensible defaults.
 * @param overrides Fields to change.
 * @return The statement.
 */
export function makeStatement(overrides: Partial<TargetStatement> = {}): TargetStatement {
    return {
        rule: 'rule_a',
        kind: StatementKind.WARNING,
        severity: Severity.WARNING,
        message: 'Star width is high.',
        confidence: Confidence.MEASURED,
        ...overrides,
    };
}

/**
 * Builds a target report with one capture setting and the given statements.
 * @param statements The statements.
 * @param overrides Fields to change.
 * @return The report.
 */
export function makeReport(statements: TargetStatement[], overrides: Partial<TargetDatasetReport> = {}): TargetDatasetReport {
    return {
        targetId: 'M 81',
        captureSettings: [
            {
                captureSetting: { camera: 'ASI533', filterName: 'L', exposureSeconds: 60, gainSetting: 100 },
                framesTaken: 40,
                nights: 2,
                framesSetAside: 3,
                capturedSeconds: 2400,
                weightEffectiveSeconds: 2000,
                efficiencyEffectiveSeconds: 1800,
                lossAccount: {
                    goal: ScienceGoal.FAINT_POINT_SOURCE,
                    referenceName: 'best night',
                    setAsideSeconds: 180,
                    setAside: [{ cause: 'trailing', seconds: 180, recoverableWithTime: false, modelDerived: false }],
                    keptFrameDeficit: [{ cause: 'sky glow', seconds: 300, recoverableWithTime: true, modelDerived: true }],
                    causeUnknownSeconds: 60,
                    causeUnknownReason: 'no model',
                    transparencyIncluded: false,
                    transparencyReason: 'not measured',
                },
            },
        ],
        statements,
        needsAttention: { warningCount: 1, worstSeverity: Severity.WARNING },
        notes: ['A note from the library.'],
        forecastChecks: [
            {
                forecastWhenPlanned: { noiseChangeFraction: -0.1, tolerance: 0.02 },
                forecastGivenFramesTaken: { noiseChangeFraction: -0.09, tolerance: 0.02 },
                measured: { noiseChangeFraction: -0.08, tolerance: 0.02 },
                weightingMode: 'weight',
                paired: true,
                outcome: ForecastOutcome.AS_FORECAST,
            },
        ],
        contextOmittedReason: 'Sensor temperature is not stored.',
        ...overrides,
    };
}

/** A calibration recommendation. */
export const calibrationStatement = makeStatement({
    rule: 'dark_missing',
    kind: StatementKind.RECOMMENDATION,
    severity: Severity.ADVICE,
    message: 'Take new darks.',
    actionClass: ActionClass.CALIBRATION,
    proposedAction: { calibrationFrameType: 'dark', note: 'Take 20 darks.' },
});
