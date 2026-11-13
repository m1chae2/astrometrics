/**
 * @file ImageProcessingContext.tsx
 * @description Context Provider for Image Processing feature state.
 */
import React, { createContext, useContext, useState, useCallback, ReactNode } from 'react';
import { useImageProcessing, UseImageProcessingResult } from '../hooks/useImageProcessing';
import { useTargetContext } from '../../common/context/TargetContext';
import { SelectableItem } from '../../common/components/SelectableList';
import type { TargetListFilterPanelProps } from '../../common/radioList/TargetListFilterPanel';
import { useTargetFilesLogic } from '../hooks/useTargetFilesLogic';

/**
 * Extended context value including target list, processing state, and files.
 */
export interface ImageProcessingContextValue extends UseImageProcessingResult {
    isLocalTarget: boolean;
    items: SelectableItem[];
    isLoading: boolean;
    filterPanel: TargetListFilterPanelProps;
    filterText: string;
    setFilterText: (text: string) => void;
    // Files Logic
    files: ReturnType<typeof useTargetFilesLogic>;
    /**
     * Tells the provider the job history is on screen (the Analysis tab was opened),
     * so it reads the history. Until then it is not read. Safe to call more than once.
     */
    requestJobHistory: () => void;
}

const ImageProcessingContext = createContext<ImageProcessingContextValue | undefined>(undefined);

interface ImageProcessingProviderProps {
    children: ReactNode;
    isLocalTarget: boolean;
    items: SelectableItem[];
    isLoading: boolean;
    filterPanel: TargetListFilterPanelProps;
    filterText: string;
    setFilterText: (text: string) => void;
}

/**
 * Provider that wraps the Image Processing feature.
 * Consolidates all state required for the Image Processing display.
 */
export const ImageProcessingProvider: React.FC<ImageProcessingProviderProps> = ({
    children,
    isLocalTarget,
    items,
    isLoading,
    filterPanel,
    filterText,
    setFilterText
}) => {
    const { selectedTarget, reloadKey, framesReloadKey } = useTargetContext();
    const [historyWanted, setHistoryWanted] = useState(false);
    const requestJobHistory = useCallback(() => setHistoryWanted(true), []);
    const processingResult = useImageProcessing(selectedTarget, isLocalTarget, historyWanted);
    const files = useTargetFilesLogic(selectedTarget, reloadKey + framesReloadKey, isLocalTarget);

    const value: ImageProcessingContextValue = {
        ...processingResult,
        isLocalTarget,
        items,
        isLoading,
        filterPanel,
        filterText,
        setFilterText,
        files,
        requestJobHistory
    };

    return (
        <ImageProcessingContext.Provider value={value}>
            {children}
        </ImageProcessingContext.Provider>
    );
};

/**
 * Hook to consume the ImageProcessingContext.
 */
export const useImageProcessingContext = () => {
    const context = useContext(ImageProcessingContext);
    if (context === undefined) {
        throw new Error('useImageProcessingContext must be used within an ImageProcessingProvider');
    }
    return context;
};
