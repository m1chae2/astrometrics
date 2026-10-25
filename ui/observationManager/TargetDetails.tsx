import React from 'react';
import { useTargetStatus } from './hooks/useTargetStatus';
import { refreshTarget } from '../common/services/targetService';
import { ControlColumn, ParameterRow } from '../common/components/ControlLayoutComponents';
import '../common/styles/panels.css';

interface Props {
    selectedTargetId?: string;
}

import { useTargetContext } from '../common/context/TargetContext';

export const TargetDetails: React.FC<Props> = ({ selectedTargetId }) => {
    const result = useTargetStatus(selectedTargetId);
    const { forceReload } = useTargetContext();
    const status = result?.status;

    if (!selectedTargetId) {
        return <div className="target-details__placeholder">Select a target to view status</div>;
    }

    if (!status) {
        return <div className="target-details__loading">Loading status...</div>;
    }

    return (
        <div className="target-details__container">
            <div className="target-details__grid">
            <ParameterRow label="RA">
                <span className="value-text">{status.ra || '--'}</span>
            </ParameterRow>
            <ParameterRow label="DEC">
                <span className="value-text">{status.dec || '--'}</span>
            </ParameterRow>
            <ParameterRow label="Altitude">
                <span className="value-text">{status.alt || '--'}</span>
            </ParameterRow>
            <ParameterRow label="Azimuth">
                <span className="value-text">{status.az || '--'}</span>
            </ParameterRow>
            <ParameterRow label="Rise">
                <span className="value-text">{status.riseTime || '--'}</span>
            </ParameterRow>
            <ParameterRow label="Set">
                <span className="value-text">{status.setTime || '--'}</span>
            </ParameterRow>
            </div>
            <div className="target-details__actions">
                <button
                    className="btn btn--secondary"
                    onClick={async () => {
                        await refreshTarget(selectedTargetId);
                        forceReload();
                    }}
                    title="Scan the filesystem to reindex this target's files"
                >
                    Reindex Target
                </button>
            </div>
        </div>
    );
};
