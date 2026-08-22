import { useState, useEffect, useMemo, useCallback } from 'react';
import { fetchTargetFiles } from '../../common/services/targetService';
import { deleteFiles } from '../../common/services/imagingService';
import { emitToast } from '../../common/utils/emitToast';
import { matchesQuery } from '../../common/utils/searchLogic';
import { FileItem } from '../fileBrowser/FileBrowser';

/**
 * Hook to manage file listing, filtering, and deletion for a target.
 *
 * @param selectedTarget - The ID of the currently selected target.
 * @param reloadKey - A key to trigger reloading of files.
 * @returns Object containing file state and handlers.
 *
 * @example
 * const { allFiles, filteredFiles, handleDelete } = useTargetFilesLogic('target-1', 0);
 */
export const useTargetFilesLogic = (selectedTarget: string, reloadKey: number, shouldFetch: boolean = true) => {
    const [allFiles, setAllFiles] = useState<FileItem[]>([]);
    const [exposureCounts, setExposureCounts] = useState<Record<string, number>>({});
    const [fileFilterText, setFileFilterText] = useState('');
    const [selectedFile, setSelectedFile] = useState<string | null>(null);
    const [stackedImage, setStackedImage] = useState<string | null>(null);
    const [stackedSpectralTarget, setStackedSpectralTarget] = useState<string | null>(null);
    const [totalExposure, setTotalExposure] = useState<number>(0);

    // File Deletion State
    const [showFileDeleteConfirm, setShowFileDeleteConfirm] = useState(false);
    const [filesToDelete, setFilesToDelete] = useState<string[]>([]);

    const loadTargetFiles = useCallback((targetId: string) => {
        fetchTargetFiles(targetId).then(res => {
            const mappedFiles: FileItem[] = (res.files || []).map(f => ({
                path: f.path,
                name: f.name,
                camera: f.camera ?? '',
                iso: f.iso ?? '',
                exposure: f.exposure ?? '',
                filter: f.filter ?? '',
                date: f.date ?? '',
            }));

            setAllFiles(mappedFiles);
            setExposureCounts(res.exposureCounts || {});
            setStackedImage(res.stackedImage ?? null);
            setStackedSpectralTarget(res.stackedSpectralTarget ?? null);
            setTotalExposure(res.totalExposure ?? 0);
        }).catch(err => {
            console.error("Failed to load target files", err);
            setAllFiles([]);
            setStackedImage(null);
            setStackedSpectralTarget(null);
            setTotalExposure(0);
            setExposureCounts({});
        });
    }, []);


    useEffect(() => {
        setStackedImage(null);
        setStackedSpectralTarget(null);
        if (!selectedTarget || !shouldFetch) {
            setAllFiles([]);
            return;
        }
        loadTargetFiles(selectedTarget);
    }, [selectedTarget, reloadKey, loadTargetFiles, shouldFetch]);

    const filteredFiles = useMemo(() => {
        if (!fileFilterText) return allFiles;
        return allFiles.filter(f => matchesQuery(f, fileFilterText));
    }, [allFiles, fileFilterText]);

    const handleRequestDeleteFiles = (paths: string[]) => {
        setFilesToDelete(paths);
        setShowFileDeleteConfirm(true);
    };

    const confirmDeleteFiles = async () => {
        setShowFileDeleteConfirm(false);
        if (filesToDelete.length === 0) return;

        try {
            await deleteFiles(filesToDelete, selectedTarget || undefined);
            emitToast(`Deleted ${filesToDelete.length} files.`, 'success');
            setFilesToDelete([]);
            if (selectedTarget) loadTargetFiles(selectedTarget);
        } catch (e) {
            emitToast(`Failed to delete files: ${e}`, 'error');
        }
    };

    // Multi-selection state for batch operations (delete, process, analyze)
    const [checkedFiles, setCheckedFiles] = useState<Set<string>>(new Set());

    const toggleFile = useCallback((path: string | null) => {
        if (!path) return;
        const newSet = new Set(checkedFiles);
        if (newSet.has(path)) newSet.delete(path);
        else newSet.add(path);
        setCheckedFiles(newSet);
    }, [checkedFiles]);

    const toggleAllFiles = useCallback((filesToToggle?: FileItem[]) => {
        const targets = filesToToggle || filteredFiles;
        if (targets.length === 0) return;

        const allChecked = targets.every(f => checkedFiles.has(f.path));
        const newSet = new Set(checkedFiles);

        if (allChecked) {
            // Uncheck only those in the current list
            targets.forEach(f => newSet.delete(f.path));
        } else {
            // Check all in the current list
            targets.forEach(f => newSet.add(f.path));
        }
        setCheckedFiles(newSet);
    }, [checkedFiles, filteredFiles]);

    return {
        allFiles,
        filteredFiles,
        exposureCounts,
        fileFilterText,
        setFileFilterText,
        selectedFile,
        setSelectedFile,
        showFileDeleteConfirm,
        setShowFileDeleteConfirm,
        filesToDelete,
        handleRequestDeleteFiles,
        confirmDeleteFiles,
        checkedFiles,
        setCheckedFiles,
        toggleFile,
        toggleAllFiles,
        stackedImage,
        stackedSpectralTarget,
        totalExposure
    };
};
