/**
 * @file useTargetActions.ts
 * @description Shared hook for target-related actions (save, delete, create).
 */
import { useTargetContext } from '../context/TargetContext';
import { fetchTargetObject, updateTargetById, deleteObject, createTarget } from '../services/targetService';
import { emitToast } from '../utils/emitToast';
import { emit as emitEvent } from '../utils/eventBus';
import { reportError } from '../utils/reportError';

/**
 * Shared hook for common target operations.
 * Consolidates business logic that was previously duplicated across displays.
 */
export function useTargetActions() {
    const {
        selectedTarget, setSelectedTarget, pendingTarget, setPendingTarget,
        catalogId, commonName, invalidate
    } = useTargetContext();

    const activeTarget = pendingTarget || selectedTarget;

    /**
     * Saves changes made to the currently selected target.
     */
    const saveTarget = async (): Promise<void> => {
        if (!activeTarget) {
            emitToast('No target selected', 'error');
            return;
        }
        try {
            // Fetch current serialized object to pick up values we don't edit directly.
            const existing = await fetchTargetObject(activeTarget);
            const existingObj = (existing ?? {}) as Record<string, unknown>;

            const objectInfo: Record<string, unknown> = {};
            const newId = catalogId || activeTarget;
            objectInfo['id'] = String(newId);

            // Handle common name priority
            objectInfo['common_name'] = commonName ||
                (existingObj['common_name'] as string) ||
                (existingObj['commonName'] as string) ||
                '';

            // Preserve equipment and exposure settings
            if (existingObj['main_camera']) objectInfo['main_camera'] = existingObj['main_camera'];
            if (existingObj['main_scope']) objectInfo['main_scope'] = existingObj['main_scope'];

            const expVal = existingObj['exposure_sec'] ?? existingObj['exposure'];
            if (expVal !== undefined) {
                objectInfo['exposure_sec'] = expVal;
            }

            await updateTargetById(activeTarget, objectInfo);

            // Notify other components that targets have been updated.
            emitEvent('targetsUpdated', { id: activeTarget });
            emitToast(`Saved changes to ${newId}`, 'success', 'database');

            // Update selection if the ID has changed.
            if (newId !== activeTarget) {
                setSelectedTarget(newId);
            }
            invalidate('targets');
        } catch (err) {
            reportError(err, 'saveTarget');
        }
    };

    /**
     * Deletes the currently selected target.
     */
    const confirmDeleteTarget = async (): Promise<void> => {
        if (!activeTarget) return;
        try {
            await deleteObject(activeTarget);
            setSelectedTarget('');
            setPendingTarget('');
            invalidate('targets');
        } catch (err) {
            reportError(err, 'deleteTarget');
        }
    };

    /**
     * Creates a new target in the catalog.
     */
    const handleCreateTarget = async (name: string, ra?: string, dec?: string): Promise<void> => {
        try {
            await createTarget(name, ra, dec);
            invalidate('targets');
            emitToast(`Created target ${name}`, 'success');
        } catch (err) {
            reportError(err, 'createTarget');
        }
    };

    return { saveTarget, confirmDeleteTarget, handleCreateTarget };
}
