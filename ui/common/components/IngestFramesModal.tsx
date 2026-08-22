/**
 * @file IngestFramesModal.tsx
 * @description Modal dialog component for selecting and ingesting raw frame files from the remote telescope repository.
 * It provides target details configuration, dynamic file selection, and displays real-time ingestion log status.
 */

import React, { useEffect } from 'react';
import { BaseModal } from './BaseModal';
import { SectionPanel } from './SectionPanel';
import { IngestionState } from '../hooks/useIngestionManager';
import '../styles/entry.css';
import '../styles/layout.css';
import '../styles/ingestFramesModal.css';

interface IngestFramesModalProps {
    isOpen: boolean;
    onClose: () => void;
    ingestionState: IngestionState;
    onIngestComplete?: (targetName: string) => void;
}

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
        progress,
        logs,
        scanningRemote,
        startIngestionJob,
        scanRemote,
        resetState,
        isActive
    } = ingestionState;

    // Auto-scan on open if needed
    useEffect(() => {
        if (isOpen && ingestionState.remoteFolders.length === 0) {
            scanRemote();
        }
    }, [isOpen, ingestionState.remoteFolders.length, scanRemote]);

    // Cleanup Only on COMPLETE success + Close?
    // User requested persistence, so we do NOT reset on close.
    // However, if we want to reset for a NEW clean run, we might need a "Reset" button or specific logic.
    // For now, let's allow manual reset via Close if nOt running?
    // User said: "If I close that window... process should continue... If I reopen... should show current process"
    // This implies we KEEP state.

    // Track if complete callback was already fired for the current ingestion run
    const completedFiredRef = React.useRef(false);

    // Check completion callback
    useEffect(() => {
        let timer: any = null;
        if (status === 'completed') {
            if (!completedFiredRef.current) {
                completedFiredRef.current = true;
                if (onIngestComplete) {
                    timer = setTimeout(() => {
                        onIngestComplete(targetName);
                        // Close the modal after completion to allow further interaction
                        onClose();
                    }, 1000);
                }
            }
        } else {
            completedFiredRef.current = false;
        }

        return () => {
            if (timer) clearTimeout(timer);
        };
    }, [status, onIngestComplete, targetName, onClose]);


    if (!isOpen) return null;

    const isRunning = isActive;

    const footerButtons = (
        <>
            <button className="btn btn--secondary" onClick={onClose}>Close</button>
            {!isRunning && status !== 'completed' && (
                <button className="btn btn--primary" onClick={startIngestionJob} disabled={scanningRemote}>
                    Start Ingestion
                </button>
            )}
            {/* Optional Reset Button if completed? */}
            {status === 'completed' && (
                <button className="btn btn--text" onClick={resetState}>Start New</button>
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
                            <input
                                type="text"
                                className="entry"
                                value={targetName}
                                onChange={e => setTargetName(e.target.value)}
                                disabled={isRunning}
                                placeholder="e.g. M 42"
                            />
                        </div>
                        <div className="form-group">
                            <label>Telescope</label>
                            <input
                                type="text"
                                className="entry"
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
                            <div className="text-muted ingest-modal__text-padded">Loading files...</div>
                        ) : remoteFiles.length > 0 ? (
                            <div className="ingest-modal__file-selection-container">
                                <div className="ingest-modal__file-selection-header">
                                    <label className="ingest-modal__label-zero-margin">Select Files ({selectedFiles.size} / {remoteFiles.length})</label>
                                    <button
                                        className="btn-link"
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
                                    </button>
                                </div>
                                <div className="modal-list-container ingest-modal__list-container">
                                    {remoteFiles.map(file => (
                                        <label key={file} className={`ingest-modal__file-row ${isRunning ? 'ingest-modal__file-row--default' : 'ingest-modal__file-row--clickable'}`}>
                                            <input
                                                type="checkbox"
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
                            {progress && <div className="ingest-progress">{progress}</div>}
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
