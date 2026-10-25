/**
 * @file PackageEditor.tsx
 * @description The package editor: Image Planner, Dither Settings and Image List work on the one
 * package picked in the package library. The Image List shows the package's exposure rows, the
 * Image Planner adds rows, Dither Settings edits its dither and "Add to Sequence" adds the package
 * to the session. With no package picked, each panel says "Choose a package or New package".
 */
import React, { useRef, useState } from 'react';
import { ImagePlanner } from './ImagePlanner';
import { ImageList } from './ImageList';
import { DitherSettings } from './DitherSettings';
import { LastImageViewer } from './LastImageViewer';
import { PlanItem } from './context/PlanningContext';
import { SectionPanel } from '../common/components/SectionPanel';
import { ConfirmDialog } from '../common/components/ConfirmDialog';
import { EmptyState } from '../common/components/EmptyState';
import { captureTestFrame } from '../common/services/imaging/captureService';
import { emitToast } from '../common/utils/emitToast';
import { DitherConfig, ExposureRequest, FilterType, ObservationPackage } from '../common/types/backendTypes';
import { ditherValuesOf, frameTypeFromLabel, labelFromFrameType } from './packageModel';
import '../common/styles/panels.css';
import './packageLibrary.css';

/** The words shown by each editor panel while no package is picked. */
export const NO_PACKAGE_MESSAGE = 'Choose a package or New package';

export interface PackageEditorProps {
    /** The package being edited, or null when none is picked. */
    editedPackage: ObservationPackage | null;
    onAddExposure: (request: ExposureRequest) => void;
    onUpdateExposure: (index: number, request: ExposureRequest) => void;
    onRemoveExposure: (index: number) => void;
    onChangeDither: (dither: DitherConfig) => void;
    /** Called when the user chooses Add to Sequence (or Modify) with the package as edited. */
    onAddToSequence: (editedPackage: ObservationPackage) => void;
    /** True while a queued sequence is being modified instead of a library package. */
    isEditingQueueEntry?: boolean;
    onCancelEdit?: () => void;
    /** Text of a refused save (for example a revision conflict), or null. Stays until dismissed. */
    errorMessage?: string | null;
    onDismissError?: () => void;
    /** Called after the user confirms Delete for the shown library package. Omit to hide Delete. */
    onDeletePackage?: (editedPackage: ObservationPackage) => void;
}

/**
 * Shows the package editor panels and the Last Image panel.
 * @param props See {@link PackageEditorProps}.
 * @return The editor element.
 */
export const PackageEditor: React.FC<PackageEditorProps> = ({
    editedPackage, onAddExposure, onUpdateExposure, onRemoveExposure, onChangeDither,
    onAddToSequence, isEditingQueueEntry = false, onCancelEdit, errorMessage = null, onDismissError,
    onDeletePackage,
}) => {
    /** Index of the exposure row being edited in the Image Planner, if any. */
    const [editingIndex, setEditingIndex] = useState<number | null>(null);

    // Lifted state of the Image Planner's inputs.
    const [imgType, setImgType] = useState('Light');
    const [imgCount, setImgCount] = useState(1);
    const [imgExposure, setImgExposure] = useState(60);
    const [imgFilter, setImgFilter] = useState('L');
    const [imgDelay, setImgDelay] = useState(0);

    /** True while the Delete confirmation is open. */
    const [isConfirmingDelete, setIsConfirmingDelete] = useState(false);
    /** The Delete button, so focus can return to it when the dialog is cancelled. */
    const deleteButtonRef = useRef<HTMLButtonElement>(null);

    const requests = editedPackage?.exposure_requests ?? [];
    const listItems: PlanItem[] = requests.map((request, index) => ({
        id: String(index),
        type: labelFromFrameType(request.frameType),
        count: request.count,
        exposure: request.exposureSec,
        filter: request.filter ?? FilterType.NONE,
        delay: request.delaySec ?? 0,
        name: undefined,
    }));
    const editingItem = editingIndex !== null ? listItems[editingIndex] ?? null : null;
    const dither = ditherValuesOf(editedPackage?.dither_config);

    /** Adds a new row, or replaces the row being edited. */
    const handleAddRow = (type: string, count: number, exposure: number, filter: string, delay: number) => {
        const request: ExposureRequest = {
            frameType: frameTypeFromLabel(type),
            filter: filter as FilterType,
            exposureSec: exposure,
            count,
            delaySec: delay,
        };
        if (editingIndex !== null) {
            onUpdateExposure(editingIndex, request);
            setEditingIndex(null);
        } else {
            onAddExposure(request);
        }
    };

    /** Captures one test frame with the planner's current settings. */
    const handleTestImage = async (type: string, _count: number, exposure: number, filter: string) => {
        if (!editedPackage) {
            emitToast('Choose a package before capturing a test frame', 'error');
            return;
        }
        emitToast(`Capturing test frame: ${exposure}s ${filter || ''} ${type}`.replace(/\s+/g, ' '), 'info');
        const jobId = await captureTestFrame({
            targetId: editedPackage.target_id,
            exposureSeconds: exposure,
            imageType: type,
            filterName: filter || undefined,
        });
        emitToast(
            jobId ? 'Test frame capture started' : 'Test frame capture failed',
            jobId ? 'success' : 'error'
        );
    };

    /** Closes the Delete dialog and puts focus back on the Delete button. */
    const closeDeleteDialog = () => {
        setIsConfirmingDelete(false);
        window.setTimeout(() => deleteButtonRef.current?.focus(), 0);
    };

    /** Runs the delete after the user confirmed. */
    const confirmDelete = () => {
        setIsConfirmingDelete(false);
        if (editedPackage && onDeletePackage) onDeletePackage(editedPackage);
    };

    const canAdd = !!editedPackage && requests.length > 0;

    return (
        <div className="target-planner">
            <ConfirmDialog
                open={isConfirmingDelete}
                title="Delete package"
                message={`Delete the package for ${editedPackage?.target_id ?? ''}? This cannot be undone.`}
                confirmLabel="Delete"
                onCancel={closeDeleteDialog}
                onConfirm={confirmDelete}
            />
            {errorMessage && (
                <div className="package-library__error" role="alert">
                    <span>{errorMessage}</span>
                    <button type="button" className="btn btn--tiny" onClick={onDismissError}>Dismiss</button>
                </div>
            )}
            <div className="target-planner__top-row">
                <div className="target-planner__left-column">
                    <SectionPanel title="Image Planner">
                        {editedPackage ? (
                            <ImagePlanner
                                onAdd={handleAddRow}
                                editingItem={editingItem}
                                onCancel={() => setEditingIndex(null)}
                                onTestImage={handleTestImage}
                                values={{ type: imgType, count: imgCount, exposure: imgExposure, filter: imgFilter, delay: imgDelay }}
                                onChange={(values) => {
                                    setImgType(values.type);
                                    setImgCount(values.count);
                                    setImgExposure(values.exposure);
                                    setImgFilter(values.filter);
                                    setImgDelay(values.delay);
                                }}
                            />
                        ) : (
                            <EmptyState message={NO_PACKAGE_MESSAGE} />
                        )}
                    </SectionPanel>
                    <SectionPanel title="Dither Settings">
                        {editedPackage ? (
                            <DitherSettings
                                enabled={dither.enabled}
                                everyN={dither.everyN}
                                scale={dither.pixels}
                                onChange={(enabled, everyNFrames, pixels) =>
                                    onChangeDither({ enabled, everyNFrames, pixels })}
                            />
                        ) : (
                            <EmptyState message={NO_PACKAGE_MESSAGE} />
                        )}
                    </SectionPanel>
                </div>
                <div className="target-planner__right-column">
                    <SectionPanel title="Image List">
                        {editedPackage ? (
                            <div className="target-planner__image-list-container">
                                <div className="image-list-scroll-container">
                                    <ImageList
                                        items={listItems}
                                        onRemove={(_, itemId) => onRemoveExposure(Number(itemId))}
                                        onEdit={(item) => setEditingIndex(Number(item.id))}
                                    />
                                </div>
                                <div className="target-planner__actions">
                                    {isEditingQueueEntry ? (
                                        <>
                                            <button className="btn btn--danger btn--flex-1" onClick={onCancelEdit}>
                                                Cancel
                                            </button>
                                            <button
                                                className="btn btn--primary btn--flex-1"
                                                disabled={!canAdd}
                                                onClick={() => editedPackage && onAddToSequence(editedPackage)}
                                            >
                                                Modify
                                            </button>
                                        </>
                                    ) : (
                                        <button
                                            className="btn btn--full-width"
                                            disabled={!canAdd}
                                            onClick={() => editedPackage && onAddToSequence(editedPackage)}
                                        >
                                            Add to Sequence
                                        </button>
                                    )}
                                    {onDeletePackage && !isEditingQueueEntry && (
                                        <button
                                            type="button"
                                            ref={deleteButtonRef}
                                            className="btn btn--danger"
                                            onClick={() => setIsConfirmingDelete(true)}
                                        >
                                            Delete
                                        </button>
                                    )}
                                </div>
                            </div>
                        ) : (
                            <EmptyState message={NO_PACKAGE_MESSAGE} />
                        )}
                    </SectionPanel>
                    <LastImageViewer />
                </div>
            </div>
        </div>
    );
};
