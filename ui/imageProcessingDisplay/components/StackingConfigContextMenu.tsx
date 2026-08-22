import React, { useState } from 'react';
import { BaseModal } from '../../common/components/BaseModal';

export interface StackingConfig {
    rejectionMethod: 'sigma_clip' | 'chauvenet' | 'median';
    kappaThreshold: number;
    fwhmFloor: '80%' | '90%' | 'none';
}

export interface StackingConfigContextMenuProps {
    /** Position (x, y) where the context menu was triggered (optional in panel mode). */
    position?: { x: number; y: number } | null;
    /** Callback when user closes or cancels the modal. */
    onClose: () => void;
    /** Callback when user confirms custom stacking configuration and triggers processing. */
    onStackWithConfig: (config: StackingConfig) => void;
}

/**
 * Centered modal panel window for custom stacking parameters triggered via right click on Stack Frames.
 */
export const StackingConfigContextMenu: React.FC<StackingConfigContextMenuProps> = ({
    onClose,
    onStackWithConfig,
}) => {
    const [rejectionMethod, setRejectionMethod] = useState<'sigma_clip' | 'chauvenet' | 'median'>('sigma_clip');
    const [kappaThreshold, setKappaThreshold] = useState<number>(3.0);
    const [fwhmFloor, setFwhmFloor] = useState<'80%' | '90%' | 'none'>('80%');

    const handleApply = (e: React.FormEvent) => {
        e.preventDefault();
        onStackWithConfig({
            rejectionMethod,
            kappaThreshold,
            fwhmFloor,
        });
        onClose();
    };

    return (
        <BaseModal
            isOpen={true}
            onClose={onClose}
            title="Custom Stacking Parameters"
            className="modal--compact-square"
            footer={
                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', width: '100%' }}>
                    <button type="button" className="btn btn--secondary" onClick={onClose}>
                        Cancel
                    </button>
                    <button type="button" className="btn" onClick={handleApply}>
                        Apply & Stack Frames
                    </button>
                </div>
            }
        >
            <form onSubmit={handleApply} style={{ display: 'flex', flexDirection: 'column', gap: '16px', padding: '8px 0', overflowX: 'hidden', width: '100%', boxSizing: 'border-box' }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', width: '100%' }}>
                    <label style={{ fontSize: '0.85rem', fontWeight: 600, color: '#aaa' }}>Rejection Algorithm:</label>
                    <select
                        className="entry dropdown"
                        value={rejectionMethod}
                        onChange={(e) => setRejectionMethod(e.target.value as any)}
                    >
                        <option value="sigma_clip">Adaptive Sigma Clipping</option>
                        <option value="chauvenet">Chauvenet Criterion</option>
                        <option value="median">Median Stacking</option>
                    </select>
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', width: '100%' }}>
                    <label style={{ fontSize: '0.85rem', fontWeight: 600, color: '#aaa' }}>
                        Kappa Threshold (σ): <span style={{ color: '#00e5ff', fontWeight: 'bold' }}>{kappaThreshold}</span>
                    </label>
                    <input
                        type="range"
                        min="1.5"
                        max="5.0"
                        step="0.1"
                        value={kappaThreshold}
                        onChange={(e) => setKappaThreshold(parseFloat(e.target.value))}
                        style={{ width: '100%', boxSizing: 'border-box' }}
                    />
                </div>

                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', width: '100%' }}>
                    <label style={{ fontSize: '0.85rem', fontWeight: 600, color: '#aaa' }}>FWHM Filter Floor:</label>
                    <select
                        className="entry dropdown"
                        value={fwhmFloor}
                        onChange={(e) => setFwhmFloor(e.target.value as any)}
                    >
                        <option value="80%">Top 80% FWHM Frames</option>
                        <option value="90%">Top 90% FWHM Frames</option>
                        <option value="none">All Surviving Frames</option>
                    </select>
                </div>
            </form>
        </BaseModal>
    );
};
