import { useState, useEffect, useMemo, useCallback } from 'react';
import { fetchTargetFiles } from '../../common/services/targetService';
import { deleteFiles } from '../../common/services/imaging/imageService';
import { emitToast } from '../../common/utils/emitToast';
import { useOnValueChange } from './useOnValueChange';
import { matchesQuery } from '../../common/utils/searchLogic';
import { FileItem } from '../fileBrowser/FileBrowser';
import { buildOpticOptions, chooseDefaultOptic, filterFilesByCamera, filterFilesByOptic } from '../utils/opticSelection';

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
    // See the matching comment in useStackingJob.ts: deriving "is loading"
    // from a pending-vs-settled key comparison (rather than a boolean this
    // hook's own effect toggles) keeps it correct the instant `shouldFetch`
    // flips true, even when that flip happens one render after some other
    // state (e.g. the shared target list confirming this target is local)
    // resolves -- a plain effect-toggled flag would read stale for that one
    // render, which is exactly the render callers gating "fully loaded" on
    // it (see ImageProcessingDisplay.tsx's app boot readiness check) would
    // otherwise sample.
    const pendingFilesKey = selectedTarget && shouldFetch ? `${selectedTarget}:${reloadKey}` : null;
    const [settledFilesKey, setSettledFilesKey] = useState<string | null>(null);
    const isLoadingFiles = pendingFilesKey !== null && settledFilesKey !== pendingFilesKey;

    const [allFiles, setAllFiles] = useState<FileItem[]>([]);
    const [exposureCounts, setExposureCounts] = useState<Record<string, number>>({});
    const [fileFilterText, setFileFilterText] = useState('');
    const [selectedFile, setSelectedFile] = useState<string | null>(null);
    const [stackedImage, setStackedImage] = useState<string | null>(null);
    const [stackedSpectralTarget, setStackedSpectralTarget] = useState<string | null>(null);
    const [totalExposure, setTotalExposure] = useState<number>(0);

    // The optic the user picked: null means "not picked yet, use the default",
    // and '' means the user chose "All optics".
    const [chosenOptic, setChosenOptic] = useState<string | null>(null);

    // The camera picked in the File Browser ('All' shows every camera). It lives
    // here so the Stack button uses the same frames the File Browser shows.
    const [selectedCamera, setSelectedCameraState] = useState('All');
    // A new camera may not have the optic that was picked, so the optic goes
    // back to its default for that camera.
    const setSelectedCamera = useCallback((camera: string) => {
        setSelectedCameraState(camera);
        setChosenOptic(null);
    }, []);

    // File Deletion State
    const [showFileDeleteConfirm, setShowFileDeleteConfirm] = useState(false);
    const [filesToDelete, setFilesToDelete] = useState<string[]>([]);

    const loadTargetFiles = useCallback((targetId: string, settledKey: string) => {
        fetchTargetFiles(targetId).then(res => {
            const mappedFiles: FileItem[] = (res.files || []).map(f => ({
                path: f.path,
                name: f.name,
                camera: f.camera ?? '',
                iso: f.iso ?? '',
                exposure: f.exposure ?? '',
                filter: f.filter ?? '',
                date: f.date ?? '',
                focalLengthMm: f.focalLengthMm ?? null,
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
        }).finally(() => {
            setSettledFilesKey(settledKey);
        });
    }, []);


    // Forget the previous target's files while rendering, before any effect runs.
    // A reset done in an effect would come one commit late, and in that commit the
    // old stack path and the old picked file would be asked for again under the
    // new target (including when a paused display is shown again after a switch).
    // Showing the display again with the same target changes nothing here.
    const [filesOwnerTarget, setFilesOwnerTarget] = useState(selectedTarget);
    if (filesOwnerTarget !== selectedTarget) {
        setFilesOwnerTarget(selectedTarget);
        setStackedImage(null);
        setStackedSpectralTarget(null);
        setSelectedFile(null);
        setAllFiles([]);
        setExposureCounts({});
        setTotalExposure(0);
        setChosenOptic(null);
        setSelectedCameraState('All');
    }

    useEffect(() => {
        if (!selectedTarget || !shouldFetch) {
            setAllFiles([]);
            return;
        }
        loadTargetFiles(selectedTarget, `${selectedTarget}:${reloadKey}`);
    }, [selectedTarget, reloadKey, loadTargetFiles, shouldFetch]);

    // Optic choices and their frame counts are for the selected camera only.
    const opticOptions = useMemo(
        () => buildOpticOptions(filterFilesByCamera(allFiles, selectedCamera)),
        [allFiles, selectedCamera],
    );
    const defaultOptic = useMemo(() => chooseDefaultOptic(allFiles, selectedCamera), [allFiles, selectedCamera]);
    const selectedOptic = chosenOptic ?? defaultOptic;

    const filteredFiles = useMemo(() => {
        const opticFiles = filterFilesByOptic(filterFilesByCamera(allFiles, selectedCamera), selectedOptic);
        if (!fileFilterText) return opticFiles;
        return opticFiles.filter(f => matchesQuery(f, fileFilterText));
    }, [allFiles, fileFilterText, selectedCamera, selectedOptic]);

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
            if (selectedTarget) loadTargetFiles(selectedTarget, `${selectedTarget}:${reloadKey}`);
        } catch (e) {
            emitToast(`Failed to delete files: ${e}`, 'error');
        }
    };

    // Multi-selection state for batch operations (delete, process, analyze)
    const [checkedFiles, setCheckedFiles] = useState<Set<string>>(new Set());

    // Ticked frames belong to one target. Switching targets clears them, so a
    // stack, analysis or delete never acts on frames picked for another target.
    useOnValueChange(selectedTarget, () => {
        setCheckedFiles(new Set());
    });

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
        isLoadingFiles,
        filteredFiles,
        exposureCounts,
        fileFilterText,
        setFileFilterText,
        selectedCamera,
        setSelectedCamera,
        opticOptions,
        selectedOptic,
        setSelectedOptic: setChosenOptic,
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
