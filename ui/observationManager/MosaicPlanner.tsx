
import React, { useState, useEffect } from 'react';
import { fetchTargetObject } from '../common/services/targetService';
import { previewMosaic, createMosaic } from '../common/services/mosaicService';
import { ControlColumn, ParameterRow } from '../common/components/ControlLayoutComponents';
import '../common/styles/panels.css';

import { usePlanningContext, PlanItem } from './context/PlanningContext';
import { ImageConfig } from './ImagePlanner';
import { DitherConfig } from './DitherSettings';

interface MosaicPlannerProps {
    selectedTargetId: string;
    onMosaicCreated?: (items: PlanItem[]) => void;
    imageConfig: ImageConfig;
    ditherConfig: DitherConfig;
}

interface GridConfig {
    rows: number;
    cols: number;
    overlap: number;
}

interface MosaicPanel {
    row: number;
    col: number;
    ra_deg: number;
    dec_deg: number;
    panel_id: string;
}

// REQ: OBM-3: Mosaic Planning
export const MosaicPlanner: React.FC<MosaicPlannerProps> = ({ selectedTargetId, onMosaicCreated, imageConfig, ditherConfig }) => {
    // REQ: OBM-3.1: The display SHALL provide a Mosaic Planner interface.
    // REQ: OBM-3.2: The display SHALL allow configuration of grid dimensions (Rows x Columns).
    // REQ: OBM-3.3: The display SHALL allow configuration of panel overlap percentage.
    const [config, setConfig] = useState<GridConfig>({ rows: 2, cols: 2, overlap: 20 });
    const [loading, setLoading] = useState(false);

    const handleCreate = async () => {
        if (!selectedTargetId) return;
        setLoading(true);
        try {
            const targetData = await fetchTargetObject(selectedTargetId);
            if (!targetData) {
                console.error("Failed to fetch target data for", selectedTargetId);
                return;
            }

            const ra = parseFloat(targetData.ra as unknown as string) || 0;
            const dec = parseFloat(targetData.dec as unknown as string) || 0;
            const previewPanels = await previewMosaic(ra, dec, config);

            // REQ: OBM-3.4: The display SHALL calculate and generate the list of target coordinates for each mosaic panel.
            // REQ: OBM-3.5: The display SHALL automatically generate sequence items for each mosaic panel based on current image configuration.
            const planItems = await createMosaic(
                selectedTargetId,
                config,
                previewPanels,
                imageConfig,
                ditherConfig
            );

            if (onMosaicCreated) onMosaicCreated(planItems);

        } catch (e) {
            console.error("Mosaic creation failed", e);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="mosaic-planner">
            <ControlColumn className="mosaic-planner__controls">
                <ParameterRow label="Grid Size">
                    <div className="mosaic-planner__grid-inputs">
                        <input
                            type="number" min="1" max="10"
                            value={config.rows}
                            onChange={e => setConfig({ ...config, rows: parseInt(e.target.value) || 1 })}
                            className="entry entry--short"
                        />
                        <span>x</span>
                        <input
                            type="number" min="1" max="10"
                            value={config.cols}
                            onChange={e => setConfig({ ...config, cols: parseInt(e.target.value) || 1 })}
                            className="entry entry--short"
                        />
                    </div>
                </ParameterRow>

                <ParameterRow label="Overlap">
                    <input
                        type="number" min="0" max="80"
                        value={config.overlap}
                        onChange={e => setConfig({ ...config, overlap: parseFloat(e.target.value) || 0 })}
                        className="entry entry--fill"
                    />
                </ParameterRow>
            </ControlColumn>

            <div className="mosaic-planner__actions">
                <button
                    className="btn btn--full-width"
                    onClick={handleCreate}
                    disabled={loading || !selectedTargetId}
                >
                    {loading ? 'Processing...' : 'Create Mosaic'}
                </button>
            </div>
        </div>
    );
};
