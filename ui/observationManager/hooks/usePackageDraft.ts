/**
 * @file usePackageDraft.ts
 * @description Holds the editable copy of the package picked in the package library, and the
 * operations the package editor panels use on it (add, change or remove an exposure row, change
 * the dither). Each operation shows at once, then saves through the `persist` function (a
 * `plan:edit_package` call with the last seen revision). A refused save puts the previous copy
 * back. A package with no `persist` function (a queued sequence being modified) is not saved here.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { DitherConfig, ExposureRequest, ObservationPackage } from '../../common/types/backendTypes';
import { PackageEditFields } from './usePackageLibrary';

/** Saves changed fields of a package; resolves to the saved package, or null when refused. */
export type PersistPackage = (
    packageId: string,
    fields: PackageEditFields,
    expectedRevision: number | undefined
) => Promise<ObservationPackage | null>;

/**
 * Keeps an editable copy of a package. The copy resets when a different package object is passed.
 * @param selectedPackage The package to edit, or null when none is chosen.
 * @param persist Saves an edit, or undefined when edits are not saved.
 * @return The copy and the edit operations.
 */
export const usePackageDraft = (selectedPackage: ObservationPackage | null, persist?: PersistPackage) => {
    const [draft, setDraft] = useState<ObservationPackage | null>(selectedPackage);
    const draftRef = useRef<ObservationPackage | null>(selectedPackage);

    useEffect(() => {
        draftRef.current = selectedPackage;
        setDraft(selectedPackage);
    }, [selectedPackage]);

    /** Shows the next copy and saves it; restores the previous copy when the save is refused. */
    const apply = useCallback((next: ObservationPackage, fields: PackageEditFields) => {
        const previous = draftRef.current;
        draftRef.current = next;
        setDraft(next);
        if (!persist || !previous) return;
        void persist(previous.id, fields, previous.revision).then((saved) => {
            const settled = saved ?? previous;
            draftRef.current = settled;
            setDraft(settled);
        });
    }, [persist]);

    /** Applies a change to the exposure rows. */
    const changeRows = useCallback((change: (rows: ExposureRequest[]) => ExposureRequest[]) => {
        const current = draftRef.current;
        if (!current) return;
        const rows = change(current.exposure_requests ?? []);
        apply({ ...current, exposure_requests: rows }, { exposure_requests: rows });
    }, [apply]);

    /** Adds one exposure row at the end. */
    const addExposure = useCallback((request: ExposureRequest) => {
        changeRows((rows) => [...rows, request]);
    }, [changeRows]);

    /** Replaces the exposure row at an index. */
    const updateExposure = useCallback((index: number, request: ExposureRequest) => {
        changeRows((rows) => rows.map((existing, position) => (position === index ? request : existing)));
    }, [changeRows]);

    /** Removes the exposure row at an index. */
    const removeExposure = useCallback((index: number) => {
        changeRows((rows) => rows.filter((_, position) => position !== index));
    }, [changeRows]);

    /** Replaces the package's dither setting. */
    const setDither = useCallback((dither: DitherConfig) => {
        const current = draftRef.current;
        if (!current) return;
        apply({ ...current, dither_config: dither }, { dither_config: dither });
    }, [apply]);

    return { draft, addExposure, updateExposure, removeExposure, setDither };
};
