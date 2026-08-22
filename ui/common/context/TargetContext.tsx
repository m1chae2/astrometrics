import React, { createContext, useContext, useState, useCallback, useEffect, ReactNode } from 'react';
import { on as onEvent } from '../utils/eventBus';

export type InvalidationScope = 'targets' | 'frames' | 'all';

/**
 * Interface defining the shape of the TargetContext state.
 */
export interface TargetContextValue {
    selectedTarget: string;
    setSelectedTarget: (id: string) => void;
    pendingTarget: string;
    setPendingTarget: (id: string) => void;
    reloadKey: number;
    framesReloadKey: number;
    forceReload: () => void;
    invalidate: (scope?: InvalidationScope) => void;
    // Shared editing state
    catalogId: string;
    setCatalogId: (val: string) => void;
    commonName: string;
    setCommonName: (val: string) => void;
    raShared: string;
    setRaShared: (val: string) => void;
    decShared: string;
    setDecShared: (val: string) => void;
}

/**
 * Alias for TargetContextValue for backward compatibility with older hook signatures.
 */
export type UseTargetSelectionResult = TargetContextValue;

const TargetContext = createContext<TargetContextValue | undefined>(undefined);

interface TargetProviderProps {
    children: ReactNode;
}

export const TargetProvider: React.FC<TargetProviderProps> = ({ children }) => {
    const [selectedTarget, setSelectedTarget] = useState<string>('');
    const [pendingTarget, setPendingTarget] = useState<string>('');
    const [reloadKey, setReloadKey] = useState<number>(0);
    const [framesReloadKey, setFramesReloadKey] = useState<number>(0);

    // Shared editing state
    const [catalogId, setCatalogId] = useState<string>('');
    const [commonName, setCommonName] = useState<string>('');
    const [raShared, setRaShared] = useState<string>('');
    const [decShared, setDecShared] = useState<string>('');

    const invalidate = useCallback((scope: InvalidationScope = 'all') => {
        if (scope === 'targets' || scope === 'all') {
            setReloadKey((prev) => prev + 1);
        }
        if (scope === 'frames' || scope === 'all') {
            setFramesReloadKey((prev) => prev + 1);
        }
    }, []);

    const forceReload = useCallback(() => {
        invalidate('targets');
    }, [invalidate]);

    useEffect(() => {
        const detach = onEvent('targetsUpdated', () => {
            invalidate('targets');
        });
        return () => {
            try {
                detach();
            } catch {
                // Ignore
            }
        };
    }, [invalidate]);

    const value: TargetContextValue = {
        selectedTarget,
        setSelectedTarget,
        pendingTarget,
        setPendingTarget,
        reloadKey,
        framesReloadKey,
        forceReload,
        invalidate,
        catalogId,
        setCatalogId,
        commonName,
        setCommonName,
        raShared,
        setRaShared,
        decShared,
        setDecShared
    };

    return (
        <TargetContext.Provider value={value}>
            {children}
        </TargetContext.Provider>
    );
};

/**
 * Hook to consume the TargetContext.
 * @throws Error if used outside of a TargetProvider.
 */
export const useTargetContext = (): TargetContextValue => {
    const context = useContext(TargetContext);
    if (context === undefined) {
        throw new Error('useTargetContext must be used within a TargetProvider');
    }
    return context;
};
