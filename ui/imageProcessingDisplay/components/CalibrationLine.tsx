/**
 * @file CalibrationLine.tsx
 * @description The "Calibration" block of the Data tab, shown below the stack summary. Its first
 * job is to answer one question: am I missing calibration frames? The status chip is the only
 * status: green "Complete" when every group has darks, flats and bias that fit, a warning chip when
 * some frames are only a near match, and "Frames missing" with a list of the groups that lack darks,
 * flats or bias. Hovering or focusing the chip shows the table of every group.
 */
import React from 'react';
import type { GroupedFrameStat } from '../../common/types/backendTypes';
import { StatusChip } from '../../common/components/StatusChip';
import {
    buildCalibrationReport,
    filterGroupsByScope,
    type CalibrationGroupReport,
    type CalibrationScope,
    type CalibrationPart,
} from '../utils/calibrationLine';

interface CalibrationLineProps {
    /** The target's light-frame groups, each with the stacker's calibration match. */
    lightFrames: GroupedFrameStat[];
    /** The camera and optic the section covers; other cameras' groups are left out. Null shows all. */
    scope?: CalibrationScope | null;
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

/** Draws the table of every group, with a status chip and a count in each cell. */
const AllGroupsTable: React.FC<{ groups: CalibrationGroupReport[] }> = ({ groups }) => (
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
);

/**
 * Shows the calibration status of the target, or nothing when there are no frames.
 */
export const CalibrationLine: React.FC<CalibrationLineProps> = ({ lightFrames, scope = null }) => {
    const report = buildCalibrationReport(filterGroupsByScope(lightFrames, scope));
    if (!report) return null;
    const { groups, missing, nearMatches } = report;
    const isComplete = missing.length === 0;
    const details = <AllGroupsTable groups={groups} />;

    return (
        <div className="stack-summary calibration-report" data-testid="calibration-line">
            <div className="calibration-report__heading">
                <span className="stacked-image-panel__label">
                    {isComplete ? 'Calibration' : `Missing calibration (${missing.length})`}
                </span>
                {!isComplete && <StatusChip status="warning" label="Frames missing" details={details} />}
                {isComplete && nearMatches.length === 0 && (
                    <StatusChip status="ok" label="Complete" details={details} />
                )}
                {isComplete && nearMatches.length > 0 && (
                    <StatusChip
                        status="warning"
                        label={`Complete, ${nearMatches.length} to check`}
                        details={details}
                    />
                )}
            </div>
            {scope && (
                <p className="calibration-report__scope" data-testid="calibration-scope">{scope.label}</p>
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
        </div>
    );
};
