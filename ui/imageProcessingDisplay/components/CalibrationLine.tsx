/**
 * @file CalibrationLine.tsx
 * @description The "Calibration" block of the Data tab, shown below the stack summary. Its first
 * job is to answer one question: am I missing calibration frames? When nothing is missing it says
 * "Calibration: complete". Otherwise it lists each exposure group that lacks darks, flats or bias,
 * then the groups that only have a near match to check. "Show all groups" opens a table of every
 * group.
 */
import React, { useState } from 'react';
import type { GroupedFrameStat } from '../../common/types/backendTypes';
import { Button } from '../../common/components/Button';
import { StatusChip } from '../../common/components/StatusChip';
import {
    buildCalibrationReport,
    nearMatchDetail,
    nearMatchLine,
    type CalibrationGroupReport,
    type CalibrationPart,
} from '../utils/calibrationLine';

interface CalibrationLineProps {
    /** The target's light-frame groups, each with the stacker's calibration match. */
    lightFrames: GroupedFrameStat[];
}

/** Joins words as "darks", "darks and flats" or "darks, flats and bias". */
const joinNames = (names: string[]): string =>
    names.length < 2 ? names.join('') : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;

/** Draws one cell of the all-groups table: a chip for the status and the count. */
const PartCell: React.FC<{ part: CalibrationPart }> = ({ part }) => {
    if (part.status === 'missing') {
        return <StatusChip status="error" label="None" title={part.reason} />;
    }
    return (
        <span className="calibration-report__cell">
            <StatusChip
                status={part.status === 'near' ? 'warning' : 'ok'}
                label={String(part.count)}
                title={part.reason || `${part.count} ${part.noun} found`}
            />
        </span>
    );
};

/** Draws one row of the table with every group. */
const GroupRow: React.FC<{ group: CalibrationGroupReport }> = ({ group }) => (
    <tr>
        <th scope="row" title={group.settings}>{group.label}</th>
        {group.parts.map((part) => (
            <td key={part.noun}><PartCell part={part} /></td>
        ))}
    </tr>
);

/**
 * Shows the calibration status of the target, or nothing when there are no frames.
 */
export const CalibrationLine: React.FC<CalibrationLineProps> = ({ lightFrames }) => {
    const [showAll, setShowAll] = useState(false);
    const report = buildCalibrationReport(lightFrames);
    if (!report) return null;
    const { groups, missing, nearMatches } = report;
    const isComplete = missing.length === 0;
    const groupWord = groups.length === 1 ? 'exposure group' : 'exposure groups';

    return (
        <div className="stack-summary calibration-report" data-testid="calibration-line">
            <div className="calibration-report__heading">
                <span className="stacked-image-panel__label">
                    {isComplete ? 'Calibration' : `Missing calibration (${missing.length})`}
                </span>
                {isComplete ? (
                    <StatusChip status="ok" label="Complete" />
                ) : (
                    <StatusChip status="warning" label="Frames missing" />
                )}
            </div>
            {isComplete && nearMatches.length === 0 && (
                <p className="calibration-report__note">
                    Darks, flats and bias found for all {groups.length} {groupWord}.
                </p>
            )}
            {missing.length > 0 && (
                <ul className="calibration-report__list" data-testid="calibration-missing">
                    {missing.map((group) => {
                        const names = group.parts.filter((part) => part.status === 'missing').map((part) => part.noun);
                        return (
                            <li key={group.label + group.settings} className="calibration-report__item">
                                <span>No {joinNames(names)} for {group.label}</span>
                                <span className="calibration-report__settings">({group.settings})</span>
                            </li>
                        );
                    })}
                </ul>
            )}
            {nearMatches.length > 0 && (
                <>
                    <div className="calibration-report__subheading">Check</div>
                    <ul className="calibration-report__list" data-testid="calibration-check">
                        {nearMatches.map((group) => (
                            <li
                                key={group.label + group.settings}
                                className="calibration-report__item"
                                title={nearMatchDetail(group)}
                            >
                                <span>{nearMatchLine(group)}</span>
                                <span className="calibration-report__settings">({group.settings})</span>
                            </li>
                        ))}
                    </ul>
                </>
            )}
            <div>
                <Button
                    variant="secondary"
                    size="small"
                    pressed={showAll}
                    onClick={() => setShowAll((open) => !open)}
                >
                    {showAll ? 'Hide all groups' : 'Show all groups'}
                </Button>
            </div>
            {showAll && (
                <table className="calibration-report__table" data-testid="calibration-all-groups">
                    <thead>
                        <tr>
                            <th scope="col">Group</th>
                            <th scope="col">Darks</th>
                            <th scope="col">Flats</th>
                            <th scope="col">Bias</th>
                        </tr>
                    </thead>
                    <tbody>
                        {groups.map((group) => (
                            <GroupRow key={group.label + group.settings} group={group} />
                        ))}
                    </tbody>
                </table>
            )}
        </div>
    );
};
