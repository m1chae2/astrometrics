import React, { useState, useMemo } from 'react';
import { matchesQuery } from '../../common/utils/searchLogic';
import '../imageProcessingDisplay.css'; // Consolidated styles

export interface FileItem {
    path: string;
    name: string;
    camera: string;
    iso: string;
    exposure: string;
    filter: string;
    date: string;
    fwhmPx?: number | null;
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
}

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
                        <select
                            className="file-browser__camera-select"
                            value={selectedCamera}
                            onChange={(e) => onCameraChange(e.target.value)}
                        >
                            <option value="All">All Cameras</option>
                            {uniqueCameras.map(cam => (
                                <option key={cam} value={cam}>{cam}</option>
                            ))}
                        </select>
                    )}
                    <input
                        type="text"
                        className="entry file-browser__search-input file-browser__search-input--full"
                        placeholder="Search files... (e.g. iso>800)"
                        value={searchTerm}
                        onChange={(e) => onSearchChange(e.target.value)}
                    />
                </div>
                <button
                    className="btn btn--secondary file-browser__header-btn"
                    onClick={onShowHeader}
                    disabled={!onShowHeader}
                    title="View FITS Header Info"
                >
                    Header Info
                </button>
                {checkedFiles.size > 0 && onDeleteFiles ? (
                    <button
                        className="btn btn--danger file-browser__delete-btn"
                        onClick={handleDeleteClick}
                        title={`Delete ${checkedFiles.size} Selected Files`}
                    >
                        <svg className="file-browser__icon file-browser__icon--small" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <polyline points="3 6 5 6 21 6"></polyline>
                            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                        </svg>
                        <span>{checkedFiles.size}</span>
                    </button>
                ) : (
                    <button
                        className="file-browser__help-button"
                        onClick={() => setShowHelp(!showHelp)}
                        title="Search Filter Help"
                    >
                        ?
                    </button>
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
                            <li><code>(iso {'>'} 100 && exp {'>'} 60)</code></li>
                        </ul>
                        <div className="file-browser__help-close-container">
                            <button
                                className="btn btn--secondary file-browser__help-close-btn"
                                onClick={() => setShowHelp(false)}
                            >
                                Close
                            </button>
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
    selectedCamera
}) => {
    // REQ: IMG-2: File Browsing & Filtering
    const [sortField, setSortField] = useState<SortField>('date'); // Default sort by date
    const [sortDirection, setSortDirection] = useState<SortDirection>('desc'); // Newer first

    // State for shift-click multi-selection
    const [lastActionIndex, setLastActionIndex] = useState<number | null>(null);

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

    // REQ: IMG-2.3: The display SHALL allow sorting of the file list by any column.
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

        // REQ: IMG-2.4: The display SHALL provide an advanced search filter supporting property syntax.
        // Filter
        if (searchTerm) {
            result = result.filter(file => matchesQuery(file, searchTerm));
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
    }, [files, searchTerm, sortField, sortDirection, selectedCamera]);

    const isRejected = (path: string) => rejectedFiles.includes(path);

    return (
        <div className="file-browser">
            <div className="file-browser__list">
                {/* REQ: IMG-2.1: The display SHALL list all image files associated with the selected target. */}
                {/* REQ: IMG-2.2: The display SHALL provide columns for Filename, Camera, ISO/Gain, Exposure Time, and Status. */}
                <table className="data-table">
                    <thead>
                        <tr>
                            <th className="file-browser__checkbox-cell">
                                <input
                                    type="checkbox"
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
                            <th>FWHM</th>
                            <th>Status</th>
                        </tr>
                    </thead>
                    <tbody>
                        {sortedFiles.map((file, index) => {
                            const rejected = isRejected(file.path);
                            const isOutlier = outlierFrames.has(file.path);
                            const isChecked = checkedFiles.has(file.path);
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
                                        <input
                                            type="checkbox"
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
                                    <td>{file.fwhmPx !== undefined && file.fwhmPx !== null ? `${Number(file.fwhmPx).toFixed(2)}px` : '—'}</td>
                                    {/* REQ: IMG-2.7: The display SHALL visually distinguish "Rejected" frames from "Included" frames. */}
                                    <td className={rejected ? 'status-rejected' : isOutlier ? 'status-warning' : 'status-included'}>
                                        {rejected ? 'Rejected' : isOutlier ? '⚠️ Outlier' : 'Included'}
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
