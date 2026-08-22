/**
 * @file TargetPlanner.tsx
 * @description Main interface for configuring imaging sequences and mosaic plans for a selected target.
 * REQ: OBM-1: Session Planning
 * REQ: OBM-2: Imaging Sequence Configuration
 */
import React, { useState } from 'react';
import { ImagePlanner } from './ImagePlanner';
import { ImageList } from './ImageList';
import { TargetDetails } from './TargetDetails';
import { DitherSettings } from './DitherSettings';
import { MosaicPlanner } from './MosaicPlanner';
import { CalibrationPanel } from './CalibrationPanel';
import { LastImageViewer } from './LastImageViewer';
import { usePlanningContext, PlanItem } from './context/PlanningContext';
import { SectionPanel } from '../common/components/SectionPanel';
import { captureTestFrame } from '../common/services/imaging/captureService';
import { emitToast } from '../common/utils/emitToast';
import '../common/styles/panels.css';

interface TargetPlannerProps {
    onAddToSequence: (targetName: string, items: any[]) => void;
    selectedTargetId: string;
    planItems: PlanItem[];
    addItem: (type: string, count: number, exposure: number, filter: string, delay: number) => void;
    addItems: (items: PlanItem[]) => void;
    updateItem: (id: string, type: string, count: number, exposure: number, filter: string, delay: number) => void;
    removeItem: (itemId: string) => void;
    onCancelEdit?: () => void;
    isEditing?: boolean;
}

/**
 * Component for planning image sequences, dither, and mosaics.
 * REQ: OBM-1: Session Planning
 */
export const TargetPlanner: React.FC<TargetPlannerProps> = ({
    onAddToSequence,
    selectedTargetId,
    planItems,
    addItem,
    addItems,
    updateItem,
    removeItem,
    onCancelEdit,
    isEditing
}) => {
    // REQ: OBM-3: Target Planning
    // REQ: SR-4.2: The system SHALL plan imaging sequences (mosaics, dithering, filters).
    const [editingItem, setEditingItem] = useState<PlanItem | null>(null);

    // Lifted State for Image Planner
    const [imgType, setImgType] = useState('Light');
    const [imgCount, setImgCount] = useState(1);
    const [imgExposure, setImgExposure] = useState(60);
    const [imgFilter, setImgFilter] = useState('L');
    const [imgDelay, setImgDelay] = useState(0);

    // Lifted State for Dither Settings
    const [ditherEnabled, setDitherEnabled] = useState(false);
    const [ditherEvery, setDitherEvery] = useState(1);
    const [ditherScale, setDitherScale] = useState(2);

    const handleAddToSequenceClick = () => {
        if (!selectedTargetId) {
            return;
        }
        if (planItems.length === 0) {
            return;
        }
        onAddToSequence(selectedTargetId, planItems);
    };

    const handleEdit = (item: PlanItem) => {
        setEditingItem(item);
    };

    const handleUpdate = (type: string, count: number, exposure: number, filter: string, delay: number) => {
        if (editingItem) {
            updateItem(editingItem.id, type, count, exposure, filter, delay);
            setEditingItem(null);
        } else {
            addItem(type, count, exposure, filter, delay);
        }
    };

    const handleCancelEdit = () => {
        setEditingItem(null);
    };

    const handleTestImage = async (type: string, count: number, exposure: number, filter: string, delay: number) => {
        if (!selectedTargetId) {
            emitToast('Select a target before capturing a test frame', 'error');
            return;
        }

        emitToast(`Capturing test frame: ${exposure}s ${filter || ''} ${type}`.replace(/\s+/g, ' '), 'info');
        const jobId = await captureTestFrame({
            targetId: selectedTargetId,
            exposureSeconds: exposure,
            imageType: type,
            filterName: filter || undefined,
        });
        emitToast(
            jobId ? 'Test frame capture started' : 'Test frame capture failed',
            jobId ? 'success' : 'error'
        );
    };

    const handleMosaicCreated = (items: PlanItem[]) => {
        addItems(items);
    };

    return (
        <div className="target-planner">
            <div className="target-planner__top-row">
                <div className="target-planner__left-column">
                    <SectionPanel title="Image Planner">
                        <ImagePlanner
                            onAdd={handleUpdate}
                            editingItem={editingItem}
                            onCancel={handleCancelEdit}
                            onTestImage={handleTestImage}
                            // Controlled props
                            values={{
                                type: imgType,
                                count: imgCount,
                                exposure: imgExposure,
                                filter: imgFilter,
                                delay: imgDelay
                            }}
                            onChange={(vals) => {
                                setImgType(vals.type);
                                setImgCount(vals.count);
                                setImgExposure(vals.exposure);
                                setImgFilter(vals.filter);
                                setImgDelay(vals.delay);
                            }}
                        />
                    </SectionPanel>
                    <div className="target-planner__row">
                        <SectionPanel title="Dither Settings">
                            <DitherSettings
                                enabled={ditherEnabled}
                                everyN={ditherEvery}
                                scale={ditherScale}
                                onChange={(e, n, s) => {
                                    setDitherEnabled(e); setDitherEvery(n); setDitherScale(s);
                                }}
                            />
                        </SectionPanel>
                        <SectionPanel title="Mosaic Planner">
                            <MosaicPlanner
                                selectedTargetId={selectedTargetId}
                                onMosaicCreated={handleMosaicCreated}
                                imageConfig={{
                                    type: imgType,
                                    count: imgCount,
                                    exposure: imgExposure,
                                    filter: imgFilter,
                                    delay: imgDelay
                                }}
                                ditherConfig={{
                                    enabled: ditherEnabled,
                                    everyN: ditherEvery,
                                    scale: ditherScale
                                }}
                            />
                        </SectionPanel>
                    </div>
                    <div className="target-planner__row">
                        <CalibrationPanel />
                        <SectionPanel title="Target Status">
                            <TargetDetails selectedTargetId={selectedTargetId} />
                        </SectionPanel>
                    </div>
                </div>
                <div className="target-planner__right-column">
                    <SectionPanel title="Image List">
                        <div className="target-planner__image-list-container">
                            <div className="image-list-scroll-container">
                                <ImageList
                                    items={planItems}
                                    onRemove={(_, itemId) => removeItem(itemId)}
                                    onEdit={handleEdit}
                                />
                            </div>
                            <div className="target-planner__actions">
                                {isEditing ? (
                                    <>
                                        <button
                                            className="btn btn--danger btn--flex-1"
                                            onClick={onCancelEdit}
                                        >
                                            Cancel
                                        </button>
                                        <button
                                            className="btn btn--success btn--flex-1"
                                            disabled={!selectedTargetId || planItems.length === 0}
                                            onClick={handleAddToSequenceClick}
                                        >
                                            Modify
                                        </button>
                                    </>
                                ) : (
                                    <button
                                        className="btn btn--full-width"
                                        disabled={!selectedTargetId || planItems.length === 0}
                                        onClick={handleAddToSequenceClick}
                                    >
                                        Add to Sequence
                                    </button>
                                )}
                            </div>
                        </div>
                    </SectionPanel>
                    <LastImageViewer />
                </div>
            </div>
        </div >
    );
};
