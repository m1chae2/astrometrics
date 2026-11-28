/**
 * @file StackCheckReport.tsx
 * @description The answer of Check Frames: what stacking would do with the frames, without
 * stacking. It shows how many frames would be stacked, which would be set aside or kept with a
 * warning and why, and which darks, bias and flats each exposure group would be calibrated with.
 * The backend runs the same checks that stacking runs, so this report and the stack agree.
 */
import React from 'react';
import { Badge } from '../../common/components/Badge';
import { StatusChip } from '../../common/components/StatusChip';
import type {
    PredictedCalibration,
    PredictedFrame,
    PredictedGroup,
    StackPrediction,
} from '../../common/types/backendTypes';
import {
    calibrationFit,
    frameKindWords,
    groupLabel,
    shortCalibrationFolder,
    SET_ASIDE_PURPOSE,
    summarizeSetAside,
} from '../utils/stackCheck';
import './stackCheckReport.css';

interface StackCheckReportProps {
    /** The backend's prediction of what a stack would do. */
    prediction: StackPrediction;
}

/** Said when every frame would be set aside, so a stack would fail. */
export const NOTHING_LEFT_MESSAGE = 'Every frame would be set aside, so stacking would fail.';

/** Draws a list of frames with the reason each is listed. */
const FrameList: React.FC<{ label: string; frames: PredictedFrame[] }> = ({ label, frames }) => (
    <ul className="set-aside-list" aria-label={label}>
        {frames.map((frame) => (
            <li key={frame.path} className="set-aside-list__item">
                <div>{frameKindWords(frame)}: {frame.file}</div>
                <div className="stack-summary--muted">{frame.reason}</div>
            </li>
        ))}
    </ul>
);

/** Draws one table cell: a chip with the frame count, the folders used and any flags. */
const CalibrationCell: React.FC<{ part: PredictedCalibration }> = ({ part }) => {
    const fit = calibrationFit(part);
    const flags = [...(part.flags ?? []), ...(part.blocking_flags ?? [])];
    return (
        <td>
            <StatusChip
                status={fit}
                label={part.frame_count === 0 ? 'None' : String(part.frame_count)}
                title={flags.join(' ') || undefined}
            />
            {(part.folders ?? []).map((folder) => (
                <div key={folder} className="stack-summary--muted stack-check__folder" title={folder}>
                    {shortCalibrationFolder(folder)}
                </div>
            ))}
        </td>
    );
};

/** Draws the table of exposure groups with their darks, bias and flats. */
const GroupTable: React.FC<{ groups: PredictedGroup[] }> = ({ groups }) => (
    <table className="calibration-report__table" aria-label="Calibration frames for each exposure group">
        <thead>
            <tr>
                <th scope="col">Group</th>
                <th scope="col">Darks</th>
                <th scope="col">Bias</th>
                <th scope="col">Flats</th>
            </tr>
        </thead>
        <tbody>
            {groups.map((group) => (
                <tr key={`${group.exposure_s}-${group.night ?? ''}`}>
                    <th scope="row" title={`gain ${group.gain}, offset ${group.offset}, binning ${group.binning}`}>
                        {groupLabel(group)}
                    </th>
                    <CalibrationCell part={group.dark} />
                    <CalibrationCell part={group.bias} />
                    <CalibrationCell part={group.flat} />
                </tr>
            ))}
        </tbody>
    </table>
);

/**
 * Shows what a stack would do with the checked frames.
 */
export const StackCheckReport: React.FC<StackCheckReportProps> = ({ prediction }) => {
    const setAside = prediction.set_aside ?? [];
    const flagged = prediction.flagged ?? [];
    const groups = prediction.groups ?? [];
    const warnings = [
        ...groups.flatMap((group) => (group.warnings ?? []).map((text) => `${groupLabel(group)}: ${text}`)),
        ...(prediction.warnings ?? []),
    ];
    return (
        <div className="stack-check" role="region" aria-label="Frame check result">
            <div className="stack-check__counts" role="status">
                <Badge tone={prediction.nothing_left ? 'error' : 'ok'}>
                    {prediction.frames_kept} of {prediction.frames_selected} {prediction.kind} frames would be stacked
                </Badge>
                {setAside.length > 0 && <Badge tone="warning">{setAside.length} set aside</Badge>}
                {flagged.length > 0 && <Badge tone="info">{flagged.length} kept with a warning</Badge>}
                {prediction.camera && <span className="stack-summary--muted">{prediction.camera}</span>}
            </div>
            {prediction.nothing_left && (
                <div className="stack-summary--error" role="alert">{NOTHING_LEFT_MESSAGE}</div>
            )}
            {setAside.length > 0 && (
                <details className="stack-summary__details">
                    <summary>Set aside: {summarizeSetAside(setAside)}</summary>
                    <div className="stack-summary--muted">{SET_ASIDE_PURPOSE}</div>
                    <FrameList label="Frames stacking would set aside" frames={setAside} />
                </details>
            )}
            {flagged.length > 0 && (
                <details className="stack-summary__details">
                    <summary>Kept with a warning ({flagged.length})</summary>
                    <FrameList label="Frames stacking would keep with a warning" frames={flagged} />
                </details>
            )}
            {groups.length > 0 && <GroupTable groups={groups} />}
            {warnings.length > 0 && (
                <ul className="stack-summary__flag-list" aria-label="Warnings">
                    {warnings.map((text) => (
                        <li key={text} className="stack-summary__flag">{text}</li>
                    ))}
                </ul>
            )}
            <div className="stack-summary--muted">{prediction.note}</div>
        </div>
    );
};
