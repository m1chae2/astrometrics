/**
 * @file BrightObjectRow.tsx
 * @description The "Enhance" row of the Image Processing Display's Data tab, in the Final Stack
 * block. The stack's preview gives one small, bright, smooth object its own stretch. The person
 * picks which object that is: Target (the target itself, when it is a nebula or a galaxy), Nothing, or
 * a nebula or galaxy that SIMBAD lists inside the stack's field. The choice is saved on the target and
 * used the next time the stack or its preview is made.
 */
import React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { InfoButton } from '../../common/components/InfoButton';
import { PopoverSelect } from '../../common/components/PopoverSelect';
import type { PopoverSelectOption } from '../../common/components/PopoverSelect';
import { callBackend, BackendError } from '../../common/services/backendApi';
import { useTargetListQuery } from '../../common/queries/useTargetListQuery';
import { targetListId } from '../../common/hooks/targetListFiltering';
import type { BrightObjectChoice, PreviewBrightObject } from '../../common/types/backendTypes';

/** The tooltip on the label. */
export const ENHANCE_INFO = 'Brings out detail in a bright nebula or galaxy that would otherwise turn white in the saved picture. Used the next time you stack or post-process.';

/** The note under the row: saving the choice makes no picture. */
export const BRIGHT_OBJECT_NOTE = 'Used the next time you stack or post-process';

/** Prefix of the id of a menu entry that stands for an object, so no object name can clash with a mode. */
const OBJECT_ID_PREFIX = 'object:';

interface BrightObjectRowProps {
    /** The selected target's id. With none, the row shows nothing. */
    targetId: string | null;
    /** The target's imaging stack. With none there is nothing to list, so the row shows nothing. */
    stackPath?: string;
}

/**
 * Says in plain words why the list of objects could not be made.
 * @param error What the list call threw.
 * @return One short sentence for the row, with no raw backend text.
 */
const explainListFailure = (error: unknown): string => {
    const code = error instanceof BackendError ? error.code : undefined;
    if (code === 'not_found') return 'Restart the app to list objects in the frame.';
    if (code === 'external_service') return 'SIMBAD could not be reached, so the objects in the frame are not listed.';
    return 'The objects in the frame could not be listed.';
};

/**
 * Folds a target id so spelling differences do not matter: underscores read as spaces, case ignored.
 * @param id The target id.
 * @return The folded id.
 */
const foldTargetId = (id: string): string => id.replace(/_/g, ' ').trim().toLowerCase();

/**
 * Writes an object's SIMBAD name with single spaces (SIMBAD pads some names, such as "NGC  7635").
 * @param name SIMBAD's name for the object.
 * @return The name for the menu.
 */
const tidyName = (name: string): string => name.replace(/\s+/g, ' ').trim();

/**
 * Writes the small text after an object's name: its type and its size.
 * @param typeCode SIMBAD's type code.
 * @param majorAxisArcmin Major axis in arcminutes, or null/undefined when SIMBAD gives none.
 * @return The detail text.
 */
const describeObject = (typeCode: string | undefined, majorAxisArcmin: number | null | undefined): string => {
    const size = majorAxisArcmin ? `${majorAxisArcmin.toFixed(1)}′` : '';
    return [typeCode, size].filter(Boolean).join(', ');
};

/**
 * Shows the "Enhance" picker and the note about when the choice takes effect.
 * It lists what SIMBAD places inside the stack. A stack with no plate solution, or SIMBAD being out
 * of reach, shows the backend's message beside the two fixed choices.
 */
export const BrightObjectRow: React.FC<BrightObjectRowProps> = ({ targetId, stackPath }) => {
    const queryClient = useQueryClient();
    const { data: libraryTargets } = useTargetListQuery();
    const target = Array.isArray(libraryTargets) && targetId
        ? libraryTargets.find((entry) => foldTargetId(targetListId(entry)) === foldTargetId(targetId))
        : undefined;
    const saved: PreviewBrightObject | undefined = target?.previewBrightObject;
    const savedMode = saved?.mode ?? 'auto';

    const { data: choices, error: listError } = useQuery({
        queryKey: ['brightObjects', targetId, stackPath],
        queryFn: () => callBackend('processing:list_bright_objects', { target: targetId as string }, { silent: true }),
        enabled: Boolean(target && stackPath),
        // The backend keeps the list beside the stack, so a later read is quick; a new stack changes the key.
        staleTime: 5 * 60_000,
        retry: false,
    });

    const save = useMutation({
        mutationFn: (payload: { mode: 'auto' | 'off' | 'object'; object?: BrightObjectChoice }) =>
            callBackend('processing:set_bright_object', {
                target: targetId as string,
                mode: payload.mode,
                ...(payload.object ? {
                    simbad_id: payload.object.simbad_id,
                    ra_deg: payload.object.ra_deg,
                    dec_deg: payload.object.dec_deg,
                    ...(payload.object.major_axis_arcmin ? { major_axis_arcmin: payload.object.major_axis_arcmin } : {}),
                } : {}),
            }),
        onSuccess: (choice) => {
            queryClient.setQueryData(['targetList'], (list: unknown) => (
                Array.isArray(list)
                    ? list.map((entry) => (
                        foldTargetId(targetListId(entry)) === foldTargetId(targetId as string)
                            ? { ...entry, previewBrightObject: choice }
                            : entry
                    ))
                    : list
            ));
        },
    });

    if (!targetId || !stackPath || !target) return null;

    const objects = choices?.objects ?? [];
    const options: PopoverSelectOption[] = [
        { id: 'auto', label: 'Target', title: 'The target itself, when it is a nebula or a galaxy.' },
        { id: 'off', label: 'Nothing', title: 'Never give an object its own stretch.' },
        ...objects.map((object) => ({
            id: `${OBJECT_ID_PREFIX}${object.simbad_id}`,
            label: tidyName(object.simbad_id),
            detail: describeObject(object.object_type, object.major_axis_arcmin),
        })),
    ];
    let selectedId = savedMode === 'object' && saved?.simbadId ? `${OBJECT_ID_PREFIX}${saved.simbadId}` : savedMode;
    if (savedMode === 'object' && saved?.simbadId && !objects.some((object) => object.simbad_id === saved.simbadId)) {
        // The saved object is not in the list now (no list yet, or SIMBAD out of reach). Keep it shown.
        options.push({
            id: selectedId,
            label: tidyName(saved.simbadId),
            detail: describeObject(undefined, saved.majorAxisArcmin),
        });
    }
    if (!options.some((option) => option.id === selectedId)) selectedId = 'auto';

    /**
     * Saves the picked entry on the target.
     * @param id The id of the entry that was picked.
     */
    const handleSelect = (id: string | null) => {
        if (id === null || id === selectedId) return;
        if (id === 'auto' || id === 'off') {
            save.mutate({ mode: id });
            return;
        }
        const name = id.slice(OBJECT_ID_PREFIX.length);
        const object = objects.find((entry) => entry.simbad_id === name);
        if (object) save.mutate({ mode: 'object', object });
    };

    return (
        <div className="bright-object-row" data-testid="bright-object-row">
            <div className="bright-object-row__choice">
                <span className="stacked-image-panel__label">Enhance:</span>
                <InfoButton text={ENHANCE_INFO} label="About Enhance" />
                <PopoverSelect
                    label="Enhance"
                    options={options}
                    selectedId={selectedId}
                    disabled={save.isPending}
                    onSelect={handleSelect}
                    size="compact"
                />
            </div>
            <div className="bright-object-row__note">{BRIGHT_OBJECT_NOTE}</div>
            {choices?.message && <div className="bright-object-row__note" data-testid="bright-object-message">{choices.message}</div>}
            {listError && (
                <div className="bright-object-row__note" data-testid="bright-object-message">
                    {explainListFailure(listError)}
                </div>
            )}
            {save.isError && (
                <div className="bright-object-row--error" role="alert">Could not save the Enhance choice.</div>
            )}
        </div>
    );
};
