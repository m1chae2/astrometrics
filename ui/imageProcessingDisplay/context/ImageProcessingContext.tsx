/**
 * @file ImageProcessingContext.tsx
 * @description Context Provider for Image Processing feature state.
 * REQ: IMG-1: Image Management Interface
 * REQ: IMG-5: Automated Stacking Pipeline
 */
import React, { createContext, useContext, ReactNode } from 'react';
import { useImageProcessing, UseImageProcessingResult } from '../hooks/useImageProcessing';
import { useTargetContext } from '../../common/context/TargetContext';
import { SelectableItem } from '../../common/components/SelectableList';
import { useTargetFilesLogic } from '../hooks/useTargetFilesLogic';
import { useIngestionManager, IngestionState } from '../../common/hooks/useIngestionManager';

/**
 * Extended context value including target list, processing state, files, and ingestion.
 */
export interface ImageProcessingContextValue extends UseImageProcessingResult {
    isLocalTarget: boolean;
    items: SelectableItem[];
    filterOptions: string[];
    selectedFilterOption: string;
    setFilterOption: (option: string) => void;
    filterText: string;
    setFilterText: (text: string) => void;
    // Files Logic
    files: ReturnType<typeof useTargetFilesLogic>;
    // Ingestion Logic
    ingestion: IngestionState;
}

const ImageProcessingContext = createContext<ImageProcessingContextValue | undefined>(undefined);

interface ImageProcessingProviderProps {
    children: ReactNode;
    isLocalTarget: boolean;
    items: SelectableItem[];
    filterOptions: string[];
    selectedFilterOption: string;
    setFilterOption: (option: string) => void;
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
    filterOptions,
    selectedFilterOption,
    setFilterOption,
    filterText,
    setFilterText
}) => {
    const { selectedTarget, reloadKey, framesReloadKey } = useTargetContext();
    const processingResult = useImageProcessing(selectedTarget, isLocalTarget);
    const files = useTargetFilesLogic(selectedTarget, reloadKey + framesReloadKey, isLocalTarget);
    const ingestion = useIngestionManager(selectedTarget);

    const value: ImageProcessingContextValue = {
        ...processingResult,
        isLocalTarget,
        items,
        filterOptions,
        selectedFilterOption,
        setFilterOption,
        filterText,
        setFilterText,
        files,
        ingestion
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
