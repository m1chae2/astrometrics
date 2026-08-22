import React, { ReactNode } from 'react';
import { GenericDisplayLayout } from '../common/components/GenericDisplayLayout';
import { ConfirmDialog } from '../common/components/ConfirmDialog';
import { IngestFramesModal } from "../common/components/IngestFramesModal";
import { IngestionState } from '../common/hooks/useIngestionManager';
import { AddTargetModal } from '../common/components/AddTargetModal';
import { FitsHeaderModal } from '../common/components/FitsHeaderModal';
import './imageProcessingDisplay.css';

/**
 * Props for the ImageProcessingLayout component.
 */
interface ImageProcessingLayoutProps {
    leftPanel: ReactNode;
    centerPanel: ReactNode;
    rightPanel: ReactNode;
    // Modals state
    isAddModalOpen: boolean;
    onCloseAddModal: () => void;
    onAddTargetSubmit: (name: string, ra: string, dec: string) => Promise<void>;

    showDeleteConfirm: boolean;
    targetToDelete: string; // The specific target name for the message
    onConfirmDelete: () => void;
    onCancelDelete: () => void;
    onIngestComplete: (targetName: string) => void;

    showFileDeleteConfirm: boolean;
    fileDeleteCount: number;
    onConfirmFileDelete: () => void;
    onCancelFileDelete: () => void;

    isIngestModalOpen: boolean;
    onCloseIngestModal: () => void;
    ingestionState: IngestionState;

    isHeaderModalOpen: boolean;
    onCloseHeaderModal: () => void;
    targetId: string | null;
    selectedHeaderPath: string | null;
}

/**
 * Pure Layout component for Image Processing Display.
 * Orchestrates positions of panels and global modals.
 */
export const ImageProcessingLayout: React.FC<ImageProcessingLayoutProps> = ({
    leftPanel,
    centerPanel,
    rightPanel,
    isAddModalOpen,
    onCloseAddModal,
    onAddTargetSubmit,
    showDeleteConfirm,
    targetToDelete,
    onConfirmDelete,
    onCancelDelete,
    showFileDeleteConfirm,
    fileDeleteCount,
    onConfirmFileDelete,
    onCancelFileDelete,
    isIngestModalOpen,
    onCloseIngestModal,
    onIngestComplete,
    ingestionState,
    isHeaderModalOpen,
    onCloseHeaderModal,
    targetId,
    selectedHeaderPath
}) => {
    return (
        <>
            <AddTargetModal
                isOpen={isAddModalOpen}
                onClose={onCloseAddModal}
                onAdd={onAddTargetSubmit}
            />
            <GenericDisplayLayout
                className="image-processing-display"
                leftPanel={leftPanel}
                centerPanel={centerPanel}
                rightPanel={rightPanel}
            />
            <ConfirmDialog
                open={showDeleteConfirm}
                title="Delete Target"
                message={`Are you sure you want to delete ${targetToDelete}?`}
                onConfirm={onConfirmDelete}
                onCancel={onCancelDelete}
            />
            <ConfirmDialog
                open={showFileDeleteConfirm}
                title="Delete Files"
                message={`Are you sure you want to delete ${fileDeleteCount} files? This cannot be undone.`}
                onConfirm={onConfirmFileDelete}
                onCancel={onCancelFileDelete}
            />
            <IngestFramesModal
                isOpen={isIngestModalOpen}
                onClose={onCloseIngestModal}
                ingestionState={ingestionState}
                onIngestComplete={onIngestComplete}
            />
            <FitsHeaderModal
                isOpen={isHeaderModalOpen}
                onClose={onCloseHeaderModal}
                targetId={targetId}
                filePath={selectedHeaderPath}
            />
        </>
    );
};
