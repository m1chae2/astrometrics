/**
 * @file IngestFramesModal.tsx
 * @description Modal dialog component for selecting and ingesting raw frame files from the remote telescope repository.
 * It provides target details configuration, dynamic file selection, and displays real-time ingestion log status.
 */

import React, { useEffect } from 'react';
import { TextInput } from './TextInput';
import { Checkbox } from './Checkbox';
import { BaseModal } from './BaseModal';
import { SectionPanel } from './SectionPanel';
import { Skeleton } from './Skeleton';
import { IngestionState } from '../hooks/useIngestionManager';
import '../styles/layout.css';
import '../styles/ingestFramesModal.css';
import { Button } from './Button';

interface IngestFramesModalProps {
    isOpen: boolean;
    onClose: () => void;
    ingestionState: IngestionState;
    onIngestComplete?: (targetName: string) => void;
}

/** How long to show the "completed" status before reporting completion, in milliseconds. */
const COMPLETION_CLOSE_DELAY_MS = 1000;

/**
 * Extracts only the trailing filename from a full filesystem or web path.
 * Supports both forward and backward slashes as path separators.
 *
 * @param path The full file path string.
 * @returns The trailing filename of the file.
 */
const getFilename = (path: string): string => {
    return path.split(/[/\\]/).pop() || path;
};

/**
 * The calibration frame types the "Calibration" meta-target can ingest.
 * Matches the folder names the backend looks for on the telescope
 * (see IngestionService._list_calibration_files).
 */
const CALIBRATION_FRAME_TYPES = ['Dark', 'Bias', 'Flat'] as const;

/**
 * Selects only the remote files belonging to one calibration frame type.
 * Calibration files are listed prefixed with their folder, e.g.
 * "Dark/foo.fits", so this matches on that prefix.
 *
 * @param remoteFiles All remote files currently listed for the target.
 * @param frameType The calibration frame type to select, e.g. "Dark".
 * @returns The subset of `remoteFiles` belonging to `frameType`.
 */
const selectCalibrationFrameType = (remoteFiles: string[], frameType: string): string[] => {
    const prefix = `${frameType}/`;
    return remoteFiles.filter(file => file.startsWith(prefix));
};

/**
 * IngestFramesModal renders the file selection and status monitoring modal during ingestion.
 *
 * @param props The props for configuring the modal window, ingestion state, and close callback.
 * @returns The rendered React element for the modal.
 */
export const IngestFramesModal: React.FC<IngestFramesModalProps> = ({
    isOpen,
    onClose,
    ingestionState,
    onIngestComplete
}) => {
    // Destructure state from hook
    const {
        targetName, setTargetName,
        telescope, setTelescope,
        remoteFolder,
        // remoteFolders, // Unused in UI currently? Or needed for autocomplete?
        fileCount,
        remoteFiles,
        selectedFiles,
        setSelectedFiles,
        isLoadingStats,
        status,
        jobId,
        progress,
        jobTargetName,
        logs,
        scanningRemote,
        startIngestionJob,
        scanRemote,
        resetState,
        isActive
    } = ingestionState;

    // Auto-refresh file list and scan remote folders whenever the modal is opened
    useEffect(() => {
        if (isOpen) {
            if (ingestionState.refreshFiles) {
                ingestionState.refreshFiles();
            }
            if (ingestionState.remoteFolders.length === 0) {
                scanRemote();
            }
        }
    }, [isOpen, ingestionState.remoteFolders.length, scanRemote, ingestionState.refreshFiles]);

    // Cleanup Only on COMPLETE success + Close?
    // User requested persistence, so we do NOT reset on close.
    // However, if we want to reset for a NEW clean run, we might need a "Reset" button or specific logic.
    // For now, let's allow manual reset via Close if nOt running?
    // User said: "If I close that window... process should continue... If I reopen... should show current process"
    // This implies we KEEP state.

    // Keep the newest callbacks and target name in refs. The parent passes new
    // function objects on almost every render. If the effect below depended on
    // them, each re-render would cancel the pending timer and the completion
    // would never fire.
    const onIngestCompleteRef = React.useRef(onIngestComplete);
    const onCloseRef = React.useRef(onClose);
    const targetNameRef = React.useRef(targetName);
    useEffect(() => {
        onIngestCompleteRef.current = onIngestComplete;
        onCloseRef.current = onClose;
        targetNameRef.current = targetName;
    });

    // Remembers which ingestion run (job) already reported completion, so each
    // run reports once. An empty string stands for "no job id known".
    const completedRunRef = React.useRef<string | null>(null);

    // Close the dialog once per run, and report completion to `onIngestComplete`
    // when a caller gave one. The app-wide `IngestionJobsContext` finishes the
    // download itself (target record, lists), so this does not depend on the
    // display being awake.
    useEffect(() => {
        if (status !== 'completed') {
            completedRunRef.current = null;
            return undefined;
        }
        const runKey = jobId ?? '';
        if (completedRunRef.current === runKey) {
            return undefined;
        }
        // Short pause so the person sees the "completed" status before the
        // dialog closes. The run counts as reported only when the timer fires: if
        // the display is paused first, the timer is cleared and starts again on resume.
        const timer = setTimeout(() => {
            completedRunRef.current = runKey;
            onIngestCompleteRef.current?.(targetNameRef.current);
            // Close the modal after completion to allow further interaction
            onCloseRef.current();
        }, COMPLETION_CLOSE_DELAY_MS);
        return () => clearTimeout(timer);
    }, [status, jobId]);


    if (!isOpen) return null;

    const isRunning = isActive;
    const isCalibrationTarget = targetName.trim().toLowerCase() === 'calibration';

    const footerButtons = (
        <>
            <Button variant="secondary" onClick={onClose}>Close</Button>
            {!isRunning && status !== 'completed' && (
                <Button variant="primary" onClick={startIngestionJob} disabled={scanningRemote}>
                    Start Ingestion
                </Button>
            )}
            {/* Optional Reset Button if completed? */}
            {status === 'completed' && (
                <Button variant="secondary" onClick={resetState}>Start New</Button>
            )}
        </>
    );

    return (
        <BaseModal
            isOpen={isOpen}
            onClose={onClose}
            title="Ingest From Telescope"
            footer={footerButtons}
            className="modal--wide"
        >
            <div className="modal-layout">

                {/* Left Column: Configuration */}
                <div className="modal-column">

                    {/* Section 1: Target Info */}
                    <SectionPanel title="Target Information">
                        <div className="form-group">
                            <label>Target Name</label>
                            <TextInput
                                value={targetName}
                                onChange={e => setTargetName(e.target.value)}
                                disabled={isRunning}
                                placeholder="e.g. M 42"
                            />
                        </div>
                        <div className="form-group">
                            <label>Telescope</label>
                            <TextInput
                                value={telescope}
                                onChange={e => setTelescope(e.target.value)}
                                disabled={isRunning}
                            />
                        </div>

                        {/* Auto-detected Stats */}
                        {fileCount !== null && (
                            <div className="form-group mb-0 spacing-top">
                                <div className="flex-between-center">
                                    <label className="ingest-modal__label-no-margin">Files Found:</label>
                                    <span className="text-large text-highlight">{fileCount}</span>
                                </div>
                                {remoteFolder && (
                                    <div className="text-small text-muted text-right-muted">
                                        Folder: {remoteFolder}
                                    </div>
                                )}
                            </div>
                        )}
                    </SectionPanel>

                </div>

                {/* Middle Column: Files */}
                <div className="modal-column ingest-modal__column--file-selection">
                    <SectionPanel title="File Selection" className="panel--flex-grow">
                        {isLoadingStats ? (
                            <Skeleton variant="list" lines={1} count={6} label="Loading files" />
                        ) : remoteFiles.length > 0 ? (
                            <div className="ingest-modal__file-selection-container">
                                <div className="ingest-modal__file-selection-header">
                                    <label className="ingest-modal__label-zero-margin">Select Files ({selectedFiles.size} / {remoteFiles.length})</label>
                                    <div className="ingest-modal__file-selection-actions">
                                        <Button
                                            variant="secondary"
                                            size="small"
                                            onClick={() => ingestionState.refreshFiles && ingestionState.refreshFiles()}
                                            disabled={isRunning || isLoadingStats}
                                            title="Refresh file list from telescope"
                                        >
                                            ↻ Refresh
                                        </Button>
                                        <Button
                                            variant="ghost"
                                            className="ingest-modal__link"
                                            onClick={() => {
                                                if (selectedFiles.size === remoteFiles.length) {
                                                    setSelectedFiles(new Set());
                                                } else {
                                                    setSelectedFiles(new Set(remoteFiles));
                                                }
                                            }}
                                            disabled={isRunning}
                                        >
                                            {selectedFiles.size === remoteFiles.length ? 'Deselect All' : 'Select All'}
                                        </Button>
                                    </div>
                                </div>
                                {isCalibrationTarget && (
                                    <div className="ingest-modal__calibration-type-row">
                                        {CALIBRATION_FRAME_TYPES.map(frameType => {
                                            const framesOfType = selectCalibrationFrameType(remoteFiles, frameType);
                                            if (framesOfType.length === 0) return null;
                                            return (
                                                <Button
                                                    key={frameType}
                                                    variant="ghost"
                                                    className="ingest-modal__link"
                                                    onClick={() => setSelectedFiles(new Set(framesOfType))}
                                                    disabled={isRunning}
                                                    title={`Select only the ${framesOfType.length} ${frameType} frame(s)`}
                                                >
                                                    {frameType} Only
                                                </Button>
                                            );
                                        })}
                                    </div>
                                )}
                                <div className="modal-list-container ingest-modal__list-container">
                                    {remoteFiles.map(file => (
                                        <label key={file} className={`ingest-modal__file-row ${isRunning ? 'ingest-modal__file-row--default' : 'ingest-modal__file-row--clickable'}`}>
                                            <Checkbox
                                                checked={selectedFiles.has(file)}
                                                disabled={isRunning}
                                                onChange={() => {
                                                    const next = new Set(selectedFiles);
                                                    if (next.has(file)) {
                                                        next.delete(file);
                                                    } else {
                                                        next.add(file);
                                                    }
                                                    setSelectedFiles(next);
                                                }}
                                            />
                                            <span className="ingest-modal__filename-text" title={file}>{getFilename(file)}</span>
                                        </label>
                                    ))}
                                </div>
                            </div>
                        ) : (
                            <div className="text-muted ingest-modal__text-padded">No files found. Type a valid target to load files.</div>
                        )}
                    </SectionPanel>
                </div>

                {/* Right Column: Ingest Status */}
                <div className="modal-column--right">
                    <SectionPanel
                        title="Ingest Status"
                        className="ingest-modal__status-container"
                    >
                        <div className="ingest-modal__status-content">
                            <div className="ingest-status-row">
                                <span className="status-label">Status: </span>
                                <span className={`status-value ${status === 'running' ? 'text-warning' : 'text-success'}`}>{status || 'Idle'}</span>
                            </div>
                            {progress && (
                                <div className="ingest-progress">
                                    {jobTargetName ? `${jobTargetName}: ` : ''}{progress}
                                </div>
                            )}
                            <div className="logs modal-log-container">
                                {logs.length === 0 ? (
                                    <div className="ingest-modal__logs-message">No logs available.</div>
                                ) : (
                                    logs.map((log, i) => <div key={i}>{log}</div>)
                                )}
                            </div>
                        </div>
                    </SectionPanel>
                </div>
            </div>
        </BaseModal>
    );
};
