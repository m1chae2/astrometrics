import { UseTargetSelectionResult, useTargetContext } from '../context/TargetContext';

export type { UseTargetSelectionResult };

/**
 * Hook to manage target selection state and shared editing values.
 * Delegates to TargetContext to ensure state is shared across the application.
 */
export function useTargetSelection(): UseTargetSelectionResult {
    return useTargetContext();
}
