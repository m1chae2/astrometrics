/**
 * @file PackageEditor.tsx
 * @description The package editor: the Image Planner and the Image List work on the one package
 * picked in the package library. The Image Planner has two tabs: Exposures adds rows and Dither
 * edits the package's dither. The Image List below it shows the package's exposure rows, with
 * "Add to Sequence" (which adds the package to the session) and "Delete package" in its header.
 * Last Image takes the column on the right. With no package picked, each panel says "Choose a
 * package or New package".
 */
import React, { useEffect, useRef, useState } from 'react';
import { ImagePlanner } from './ImagePlanner';
import { ImageList } from './ImageList';
import { DitherSettings } from './DitherSettings';
import { LastImageViewer } from './LastImageViewer';
import { PlanItem } from './context/PlanningContext';
import { SectionPanel } from '../common/components/SectionPanel';
import { ConfirmDialog } from '../common/components/ConfirmDialog';
import { TabPanel, Tabs, type TabDefinition } from '../common/components/Tabs';
import { EmptyState } from '../common/components/EmptyState';
import { useCommandAvailability } from '../common/hooks/useCommandAvailability';
import { captureTestFrame } from '../common/services/imaging/captureService';
import { emitToast } from '../common/utils/emitToast';
import { DitherConfig, ExposureRequest, FilterType, ObservationPackage } from '../common/types/backendTypes';
import { ditherValuesOf, frameTypeFromLabel, labelFromFrameType } from './packageModel';
import { useAvailableFilters } from './hooks/useAvailableFilters';
import '../common/styles/panels.css';
import './packageLibrary.css';

/** The two views of the Image Planner panel. */
const PLANNER_TABS: TabDefinition[] = [
    { id: 'exposures', label: 'Exposures' },
    { id: 'dither', label: 'Dither' },
];

/** Prefix of the Image Planner tab and panel element ids. */
const PLANNER_TABS_ID_PREFIX = 'image-planner';

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
    /** Called after the user confirms Delete package for the shown library package. Omit to hide it. */
    onDeletePackage?: (editedPackage: ObservationPackage) => void;
    /** Set when the open suggestion was removed by a refresh: the notice text and the save action. */
    removedNotice?: { text: string; onSave: () => void } | null;
}

/**
 * Shows the package editor panels and the Last Image panel.
 * @param props See {@link PackageEditorProps}.
 * @return The editor element.
 */
export const PackageEditor: React.FC<PackageEditorProps> = ({
    editedPackage, onAddExposure, onUpdateExposure, onRemoveExposure, onChangeDither,
    onAddToSequence, isEditingQueueEntry = false, onCancelEdit, errorMessage = null, onDismissError,
    onDeletePackage, removedNotice = null,
}) => {
    /** Test image is a device command: greyed while Ekos controls the equipment. */
    const availability = useCommandAvailability();
    /** The Image Planner tab shown: Exposures or Dither. */
    const [plannerTab, setPlannerTab] = useState('exposures');
    /** Index of the exposure row being edited in the Image Planner, if any. */
    const [editingIndex, setEditingIndex] = useState<number | null>(null);

    // Lifted state of the Image Planner's inputs.
    const [imgType, setImgType] = useState('Light');
    const [imgCount, setImgCount] = useState(1);
    const [imgExposure, setImgExposure] = useState(60);
    const [imgFilter, setImgFilter] = useState<string>(FilterType.NONE);
    /** The observatory's configured filters, as the library names them. */
    const { filters: availableFilters } = useAvailableFilters();
    // Start the planner on the first configured filter once the list arrives.
    useEffect(() => {
        if (availableFilters.length > 0 && !availableFilters.includes(imgFilter)) setImgFilter(availableFilters[0]);
    }, [availableFilters, imgFilter]);
    const [imgDelay, setImgDelay] = useState(0);

    /** True while the Delete confirmation is open. */
    const [isConfirmingDelete, setIsConfirmingDelete] = useState(false);
    /** The Delete package button, so focus can return to it when the dialog is cancelled. */
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
        if (!availability.canCommand) return;
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
            {removedNotice && (
                <div className="package-library__error" role="alert">
                    <span>{removedNotice.text}</span>
                    <button type="button" className="btn btn--tiny btn--primary" onClick={removedNotice.onSave}>
                        Save as planned package
                    </button>
                </div>
            )}
            <div className="target-planner__top-row">
                <div className="target-planner__left-column">
                    <SectionPanel title="Image Planner" className="target-planner__planner">
                        {editedPackage ? (
                            <>
                                <Tabs
                                    tabs={PLANNER_TABS}
                                    selectedId={plannerTab}
                                    onChange={setPlannerTab}
                                    ariaLabel="Image Planner views"
                                    idPrefix={PLANNER_TABS_ID_PREFIX}
                                />
                                <TabPanel idPrefix={PLANNER_TABS_ID_PREFIX} tabId="exposures" selected={plannerTab === 'exposures'}>
                                    <ImagePlanner
                                        onAdd={handleAddRow}
                                        editingItem={editingItem}
                                        onCancel={() => setEditingIndex(null)}
                                        onTestImage={handleTestImage}
                                        testImageUnavailableReason={availability.canCommand ? null : availability.reason}
                                        filterOptions={availableFilters}
                                        values={{ type: imgType, count: imgCount, exposure: imgExposure, filter: imgFilter, delay: imgDelay }}
                                        onChange={(values) => {
                                            setImgType(values.type);
                                            setImgCount(values.count);
                                            setImgExposure(values.exposure);
                                            setImgFilter(values.filter);
                                            setImgDelay(values.delay);
                                        }}
                                    />
                                </TabPanel>
                                <TabPanel idPrefix={PLANNER_TABS_ID_PREFIX} tabId="dither" selected={plannerTab === 'dither'}>
                                    <DitherSettings
                                        enabled={dither.enabled}
                                        everyN={dither.everyN}
                                        scale={dither.pixels}
                                        onChange={(enabled, everyNFrames, pixels) =>
                                            onChangeDither({ enabled, everyNFrames, pixels })}
                                    />
                                </TabPanel>
                            </>
                        ) : (
                            <EmptyState message={NO_PACKAGE_MESSAGE} />
                        )}
                    </SectionPanel>
                    <SectionPanel
                        title="Image List"
                        className="target-planner__image-list"
                        headerContent={editedPackage ? (
                            <span className="target-planner__header-actions">
                                {isEditingQueueEntry ? (
                                    <>
                                        <button type="button" className="btn btn--tiny" onClick={onCancelEdit}>
                                            Cancel
                                        </button>
                                        <button
                                            type="button"
                                            className="btn btn--tiny btn--primary"
                                            disabled={!canAdd}
                                            onClick={() => editedPackage && onAddToSequence(editedPackage)}
                                        >
                                            Modify
                                        </button>
                                    </>
                                ) : (
                                    <button
                                        type="button"
                                        className="btn btn--tiny btn--primary"
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
                                        className="btn btn--tiny btn--danger"
                                        onClick={() => setIsConfirmingDelete(true)}
                                    >
                                        Delete package
                                    </button>
                                )}
                            </span>
                        ) : undefined}
                    >
                        {editedPackage ? (
                            <div className="image-list-scroll-container">
                                <ImageList
                                    items={listItems}
                                    onRemove={(_, itemId) => onRemoveExposure(Number(itemId))}
                                    onEdit={(item) => setEditingIndex(Number(item.id))}
                                />
                            </div>
                        ) : (
                            <EmptyState message={NO_PACKAGE_MESSAGE} />
                        )}
                    </SectionPanel>
                </div>
                <div className="target-planner__right-column">
                    <LastImageViewer />
                </div>
            </div>
        </div>
    );
};
