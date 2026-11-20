import React, { useState, useMemo } from 'react';
import { matchesQuery } from '../../common/utils/searchLogic';
import { EmptyState } from '../../common/components/EmptyState';
import { PopoverSelect } from '../../common/components/PopoverSelect';
import { Button } from '../../common/components/Button';
import { ConfirmDialog } from '../../common/components/ConfirmDialog';
import { StatusChip } from '../../common/components/StatusChip';
import type { FrameStatusInfo } from '../utils/frameStatus';
import { Checkbox } from '../../common/components/Checkbox';
import { TextInput } from '../../common/components/TextInput';
import { buildOpticOptions, filterFilesByCamera } from '../utils/opticSelection';
import '../imageProcessingDisplay.css'; // Consolidated styles

/** Why a frame is marked "Unusual brightness" (shown when you hover over it). */
const UNUSUAL_BRIGHTNESS_TOOLTIP =
    "This frame's reference stars were much brighter or dimmer than in the night's other frames " +
    '(more than 3 standard deviations from the typical frame), so the variable-star search left it out. ' +
    'It is still in the stack.';

export interface FileItem {
    path: string;
    name: string;
    camera: string;
    iso: string;
    exposure: string;
    filter: string;
    date: string;
    /** Focal length in mm, when the frame header has one. */
    focalLengthMm?: number | null;
}

export interface FileBrowserProps {
    files: FileItem[];
    rejectedFiles: string[];
    onSelectFile: (path: string | null) => void;
    onDeleteFiles?: (paths: string[]) => void;
    selectedFile: string | null;
    searchTerm: string;
    onSearchChange: (term: string) => void;
    // New props for lifted state
    checkedFiles: Set<string>;
    onToggleFile: (path: string | null) => void;
    onToggleAll: (files?: FileItem[]) => void;
    // Lifted Camera State
    selectedCamera: string;
    /** Each frame's status by file path. A frame with none reads "Included". */
    frameStatuses?: ReadonlyMap<string, FrameStatusInfo>;
    /** Frames the stacker set aside. They are listed after the target's own frames and cannot be ticked. */
    setAsideFiles?: FileItem[];
    /** Called after the person confirms putting a set-aside frame back. */
    onPutBackFrame?: (path: string) => void;
}

/** The status shown for a frame the backend has not described. */
const DEFAULT_STATUS: FrameStatusInfo = {
    kind: 'included',
    label: 'Included',
    tone: 'ok',
    tooltip: 'This frame is in the last stack.',
    filterText: 'included',
    restorable: false,
    restoreNote: '',
};

type SortField = 'name' | 'camera' | 'exposure' | 'iso' | 'date' | 'filter';
type SortDirection = 'asc' | 'desc';

/**
 * FileBrowserToolbar Component
 *
 * Separated toolbar to allow flexible placement (e.g., in a panel header).
 */
export const FileBrowserToolbar: React.FC<{
    files: FileItem[];
    searchTerm: string;
    onSearchChange: (term: string) => void;
    selectedCamera: string;
    onCameraChange: (cam: string) => void;
    /** The chosen focal length as text ('' for all optics). */
    selectedOptic?: string;
    /** Called with the chosen focal length as text, or '' for all optics. */
    onOpticChange?: (optic: string) => void;
    checkedFiles: Set<string>;
    onDeleteFiles?: (paths: string[]) => void;
    onShowHeader?: () => void;
    className?: string;
}> = ({
    files,
    searchTerm,
    onSearchChange,
    selectedCamera,
    onCameraChange,
    selectedOptic = '',
    onOpticChange,
    checkedFiles,
    onDeleteFiles,
    onShowHeader,
    className = ""
}) => {
        const [showHelp, setShowHelp] = useState(false);

        // Computed unique cameras for filter dropdown
        const uniqueCameras = useMemo(() => {
            const cameras = new Set(files.map(f => f.camera));
            return Array.from(cameras).sort();
        }, [files]);

        // Optic choices (with counts) are for the selected camera, and appear
        // only when that camera's frames come from two or more optics.
        const opticOptions = useMemo(
            () => buildOpticOptions(filterFilesByCamera(files, selectedCamera)),
            [files, selectedCamera],
        );

        const handleDeleteClick = (e: React.MouseEvent) => {
            e.stopPropagation();
            if (onDeleteFiles && checkedFiles.size > 0) {
                onDeleteFiles(Array.from(checkedFiles));
            }
        };

        return (
            <div className={`file-browser__toolbar ${className}`}>
                <div className="file-browser__search-container">
                    {uniqueCameras.length > 0 && (
                        <PopoverSelect
                            label="Camera"
                            options={uniqueCameras.map((camera) => ({ id: camera, label: camera }))}
                            selectedId={selectedCamera === 'All' ? null : selectedCamera}
                            onSelect={(id) => onCameraChange(id ?? 'All')}
                            emptyLabel="All Cameras"
                            size="compact"
                        />
                    )}
                    {onOpticChange && opticOptions.length > 0 && (
                        <PopoverSelect
                            label="Optic"
                            options={opticOptions}
                            selectedId={selectedOptic || null}
                            onSelect={(id) => onOpticChange(id ?? '')}
                            emptyLabel="All optics"
                            size="compact"
                        />
                    )}
                    <TextInput
                        size="small"
                        className="file-browser__search-input"
                        placeholder="Search files... (e.g. iso>800)"
                        value={searchTerm}
                        onChange={(e) => onSearchChange(e.target.value)}
                    />
                </div>
                <Button
                    variant="secondary"
                    size="small"
                    className="file-browser__header-btn"
                    onClick={onShowHeader}
                    disabled={!onShowHeader}
                    title="View FITS Header Info"
                >
                    Header Info
                </Button>
                {checkedFiles.size > 0 && onDeleteFiles ? (
                    <Button
                        variant="danger"
                        size="small"
                        className="file-browser__delete-btn"
                        onClick={handleDeleteClick}
                        title={`Delete ${checkedFiles.size} Selected Files`}
                    >
                        <svg className="file-browser__icon file-browser__icon--small" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <polyline points="3 6 5 6 21 6"></polyline>
                            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                        </svg>
                        <span>{checkedFiles.size}</span>
                    </Button>
                ) : (
                    <Button
                        variant="ghost"
                        size="square"
                        className="info-button__trigger"
                        aria-label="Search filter help"
                        onClick={() => setShowHelp(!showHelp)}
                        title="Search Filter Help"
                    >
                        ?
                    </Button>
                )}
                {showHelp && checkedFiles.size === 0 && (
                    <div className="search-help-popup file-browser__help-popup">
                        <h4 className="file-browser__help-title">Search Filters</h4>
                        <p className="file-browser__help-text">
                            Use <b>property operator value</b> syntax.
                        </p>
                        <ul className="file-browser__help-list">
                            <li><code>cam: zwo</code> (Camera contains "zwo")</li>
                            <li><code>iso {'>'} 800</code> (ISO greater than 800)</li>
                            <li><code>exp {'>='} 30</code> (Exposure &ge; 30s)</li>
                            <li><code>name includes dark</code></li>
                            <li><code>status:set-aside</code> (also <code>flagged</code>, <code>included</code>, <code>not-stacked</code>)</li>
                            <li><code>(iso {'>'} 100 && exp {'>'} 60)</code></li>
                        </ul>
                        <div className="file-browser__help-close-container">
                            <Button
                                variant="secondary"
                                size="small"
                                className="file-browser__help-close-btn"
                                onClick={() => setShowHelp(false)}
                            >
                                Close
                            </Button>
                        </div>
                    </div>
                )}
            </div>
        );
    };

/**
 * FileBrowser Component
 *
 * Displays a list of file items with sorting, filtering (via parent), and selection capabilities.
 * Supports deletion of selected files.
 */
export const FileBrowser: React.FC<FileBrowserProps> = ({
    files,
    rejectedFiles,
    onSelectFile,
    onDeleteFiles,
    selectedFile,
    searchTerm,
    onSearchChange,
    checkedFiles,
    onToggleFile,
    onToggleAll,
    selectedCamera,
    frameStatuses,
    setAsideFiles = [],
    onPutBackFrame
}) => {
    const [sortField, setSortField] = useState<SortField>('date'); // Default sort by date
    const [sortDirection, setSortDirection] = useState<SortDirection>('desc'); // Newer first

    // State for shift-click multi-selection
    const [lastActionIndex, setLastActionIndex] = useState<number | null>(null);

    // The set-aside frame the person asked to put back, until they confirm or cancel.
    const [putBackPath, setPutBackPath] = useState<string | null>(null);

    const statusOf = (path: string): FrameStatusInfo => frameStatuses?.get(path) ?? DEFAULT_STATUS;

    // State for user-flagged outlier frames (cosmic rays, satellite trails)
    const [outlierFrames, setOutlierFrames] = useState<Set<string>>(new Set());

    const toggleOutlierFlag = (path: string) => {
        setOutlierFrames(prev => {
            const next = new Set(prev);
            if (next.has(path)) next.delete(path);
            else next.add(path);
            return next;
        });
    };

    const handleSort = (field: SortField) => {
        if (sortField === field) {
            setSortDirection(prev => prev === 'asc' ? 'desc' : 'asc');
        } else {
            setSortField(field);
            setSortDirection('asc');
        }
    };

    const getSortIndicator = (field: SortField) => {
        if (sortField !== field) return '';
        return sortDirection === 'asc' ? ' ▲' : ' ▼';
    };

    const sortedFiles = useMemo(() => {
        let result = files;

        // Filter by Camera
        if (selectedCamera !== 'All') {
            result = result.filter(file => file.camera === selectedCamera);
        }

        // Set-aside frames follow the target's own frames, whatever camera is picked.
        // Their file names carry no camera, so the camera filter would hide them all.
        if (setAsideFiles.length > 0) {
            result = [...result, ...setAsideFiles];
        }

        // Filter
        if (searchTerm) {
            result = result.filter(file => matchesQuery({ ...file, status: (frameStatuses?.get(file.path) ?? DEFAULT_STATUS).filterText }, searchTerm));
        }

        // Clone for sorting to avoid mutating props if it was a direct reference (though filter returns new array)
        result = [...result];

        // Sort
        if (sortField) {
            result.sort((a, b) => {
                const valueA = a[sortField];
                const valueB = b[sortField];
                if ((sortField === 'exposure' || sortField === 'iso')) {
                    const numberA = parseFloat(valueA);
                    const numberB = parseFloat(valueB);
                    if (!isNaN(numberA) && !isNaN(numberB)) return sortDirection === 'asc' ? numberA - numberB : numberB - numberA;
                }
                return sortDirection === 'asc' ? valueA.localeCompare(valueB) : valueB.localeCompare(valueA);
            });
        }

        return result;
    }, [files, setAsideFiles, frameStatuses, searchTerm, sortField, sortDirection, selectedCamera]);

    const isRejected = (path: string) => rejectedFiles.includes(path);

    return (
        <div className="file-browser">
            <ConfirmDialog
                open={putBackPath !== null}
                title="Put frame back"
                message="Move this frame out of the set-aside folder and back into the target? The next stack may set it aside again."
                confirmLabel="Put back"
                onConfirm={() => {
                    if (putBackPath !== null) onPutBackFrame?.(putBackPath);
                    setPutBackPath(null);
                }}
                onCancel={() => setPutBackPath(null)}
            />
            <div className="file-browser__list">
                <table className="data-table">
                    <thead>
                        <tr>
                            <th className="file-browser__checkbox-cell">
                                <Checkbox
                                    aria-label="Select all files"
                                    checked={sortedFiles.length > 0 && sortedFiles.every(f => checkedFiles.has(f.path))}
                                    onChange={() => onToggleAll(sortedFiles)}
                                />
                            </th>
                            <th onClick={() => handleSort('name')} className="file-browser__sortable-header">
                                Filename {getSortIndicator('name')}
                            </th>
                            <th onClick={() => handleSort('date')} className="file-browser__sortable-header">
                                Date {getSortIndicator('date')}
                            </th>
                            <th onClick={() => handleSort('camera')} className="file-browser__sortable-header">
                                Camera {getSortIndicator('camera')}
                            </th>
                            <th onClick={() => handleSort('filter')} className="file-browser__sortable-header">
                                Filter {getSortIndicator('filter')}
                            </th>
                            <th onClick={() => handleSort('exposure')} className="file-browser__sortable-header">
                                Exp (s) {getSortIndicator('exposure')}
                            </th>
                            <th>Status</th>
                        </tr>
                    </thead>
                    <tbody>
                        {sortedFiles.length === 0 && (
                            <tr>
                                <td colSpan={7} className="no-data">
                                    <EmptyState message="No files for this target." />
                                </td>
                            </tr>
                        )}
                        {sortedFiles.map((file, index) => {
                            const rejected = isRejected(file.path);
                            const isOutlier = outlierFrames.has(file.path);
                            const isChecked = checkedFiles.has(file.path);
                            const status = statusOf(file.path);
                            const isSetAside = status.kind === 'set-aside';
                            const key = `${file.path}-${index}`; // Ensure uniqueness even if path duplicates exist
                            return (
                                <tr
                                    key={key}
                                    className={`${selectedFile === file.path ? 'selected' : ''} ${isOutlier ? 'row-outlier' : ''}`}
                                    onContextMenu={(e) => {
                                        e.preventDefault();
                                        toggleOutlierFlag(file.path);
                                    }}
                                    title="Left click: Select • Right click: Toggle Outlier Flag (Cosmic Rays / Satellite)"
                                    onClick={(e) => {
                                        if (e.shiftKey && lastActionIndex !== null) {
                                            const start = Math.min(lastActionIndex, index);
                                            const end = Math.max(lastActionIndex, index);
                                            const filesToToggle = sortedFiles.slice(start, end + 1);
                                            onToggleAll(filesToToggle);
                                        } else {
                                            onSelectFile(file.path);
                                        }
                                        setLastActionIndex(index);
                                    }}
                                    onMouseDown={(e) => {
                                        if (e.shiftKey) e.preventDefault();
                                    }}
                                >
                                    <td onClick={(e) => e.stopPropagation()}>
                                        <Checkbox
                                            aria-label={`Select ${file.name}`}
                                            disabled={isSetAside}
                                            checked={isChecked}
                                            onChange={() => {}}
                                            onClick={(e) => {
                                                if (e.shiftKey && lastActionIndex !== null) {
                                                    const start = Math.min(lastActionIndex, index);
                                                    const end = Math.max(lastActionIndex, index);
                                                    const filesToToggle = sortedFiles.slice(start, end + 1);
                                                    onToggleAll(filesToToggle);
                                                } else {
                                                    onToggleFile(file.path);
                                                }
                                                setLastActionIndex(index);
                                            }}
                                        />
                                    </td>
                                    <td>{file.name}</td>
                                    <td>{file.date}</td>
                                    <td>{file.camera}</td>
                                    <td>{file.filter}</td>
                                    <td>{file.exposure}</td>
                                    {/* The chip says where the frame stands in the stack; its hover text is the
                                        reason. The variable-star search can also leave a frame out. That says
                                        nothing about stacking, so it is a second chip that names what was unusual. */}
                                    <td>
                                      <div className="file-browser__status-cell">
                                        <StatusChip
                                            status={status.tone}
                                            label={status.label}
                                            title={isSetAside && !status.restorable && status.restoreNote
                                                ? `${status.tooltip} ${status.restoreNote}`
                                                : status.tooltip}
                                        />
                                        {rejected && (
                                            <StatusChip status="warning" label="Unusual brightness" title={UNUSUAL_BRIGHTNESS_TOOLTIP} />
                                        )}
                                        {isOutlier && <StatusChip status="warning" label="Outlier" title="You marked this frame as an outlier." />}
                                        {isSetAside && status.restorable && onPutBackFrame && (
                                            <Button
                                                size="small"
                                                variant="secondary"
                                                title="Move this frame back into the target"
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    setPutBackPath(file.path);
                                                }}
                                            >
                                                Put back
                                            </Button>
                                        )}
                                      </div>
                                    </td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        </div>
    );
};
