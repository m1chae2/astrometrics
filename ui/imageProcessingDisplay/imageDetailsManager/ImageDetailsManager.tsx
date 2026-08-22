import React, { useState } from 'react';
import '../imageProcessingDisplay.css';
import { LoadingSpinner } from '../../common/components/LoadingSpinner';
import { StackingConfigContextMenu, StackingConfig } from '../components/StackingConfigContextMenu';

/**
 * Props for the ImageDetailsManager component.
 */
export interface ImageDetailsManagerProps {
  /** The currently selected target ID. */
  selectedTarget?: string;
  /** The common name of the target. */
  commonName?: string;
  /** The catalog ID of the target. */
  catalogId?: string;
  /** Optional callback for updating the pending target selection. */
  setPendingTarget?: (v: string) => void;
  /** Optional callback for triggering a reload of parent state. */
  setReloadKey?: (n: number | ((n: number) => number)) => void;
  /** RA coordinate string. */
  ra?: string;
  /** DEC coordinate string. */
  dec?: string;
  /** Callback to start a processing operation. */
  onProcess?: () => void;
  /** Callback to cancel a processing operation. */
  onCancel?: () => void;
  /** Callback to add new images to the target. */
  onAddImages?: () => void;
  /** Callback to manually add a new target entry. */
  onAddTarget?: () => void;
  /** Whether a processing operation is currently in progress. */
  isProcessing?: boolean;
  /** Whether multiple camera types are selected. */
  hasMultipleCameras?: boolean;
  /** List of camera names currently selected. */
  selectedCameras?: string[];
  /** Whether no files are selected/present for processing. */
  hasNoFiles?: boolean;
  /** Callback to start analysis. */
  onAnalyze?: () => void;
  /** Whether analysis is currently in progress. */
  isAnalyzing?: boolean;
  /** Whether multiple filter types are selected. */
  hasMultipleFilters?: boolean;
  /** List of filters currently selected. */
  selectedFilters?: string[];
  /** Callback to open Ingest Frames modal. */
  onIngest?: () => void;
  /** Whether ingestion is disabled (e.g. no remote frames). */
  isIngestDisabled?: boolean;
  /** Callback to open Siril GUI. */
  onOpenSiril?: () => void;
  /** Callback to reindex target. */
  onReindex?: () => void;
  /** Callback to set a stacked property to a specific file. */
  onSetStackedProperty?: (property: 'stackedImage' | 'stackedSpectralTarget', filePath: string) => void;
}

/**
 * Summary and controls manager for the image processing view.
 */
export const ImageDetailsManager: React.FC<ImageDetailsManagerProps> = ({
  selectedTarget,
  commonName,
  catalogId,
  setPendingTarget,
  setReloadKey,
  ra,
  dec,
  onProcess,
  onCancel,
  onAddImages,
  onAddTarget,
  isProcessing,
  hasMultipleCameras,
  selectedCameras,
  hasNoFiles,
  onAnalyze,
  isAnalyzing,
  hasMultipleFilters,
  selectedFilters,
  onIngest,
  isIngestDisabled,
  onOpenSiril,
  onReindex,
  onSetStackedProperty,
}) => {
  const [contextMenuPos, setContextMenuPos] = useState<{ x: number; y: number } | null>(null);

  /** Helper to trigger Open Siril event. */
  const handleOpenSiril = (): void => {
    if (onOpenSiril) {
      onOpenSiril();
    }
  };

  return (
    <div className="image-details-manager">
      <div className="image-details-manager__controls-frame">
        {/* REQ: IMG-4: Image Analysis Tools */}
        <div className="image-details-manager__controls">
          <div className="image-details-manager__toolbar">
            {onAnalyze && !isAnalyzing && (
              <button
                className="btn"
                // REQ: IMG-4.1: The display SHALL provide an "Analyze Target" command.
                // REQ: IMG-4.3: The display SHALL present analysis results, including HFR and star counts.
                onClick={() => {
                  if (!isAnalyzing && !isProcessing) {
                    onAnalyze();
                  }
                }}
                disabled={hasNoFiles || isProcessing}
                type="button"
                title="Analyze Target"
              >
                Analyze Target
              </button>
            )}

            {isAnalyzing && onCancel && (
              <button
                className="btn btn--danger"
                onClick={onCancel}
                type="button"
              >
                Cancel
              </button>
            )}

            {onIngest && (
              <button
                className="btn"
                onClick={onIngest}
                type="button"
                disabled={isIngestDisabled}
                title={isIngestDisabled ? "No remote frames found for this target" : "Ingest from Remote/Local"}
              >
                Download Frames
              </button>
            )}

            {onProcess && !isProcessing && (
              <button
                className="btn"
                onClick={onProcess}
                onContextMenu={(e) => {
                  e.preventDefault();
                  if (!hasMultipleCameras && !hasNoFiles && !isProcessing) {
                    setContextMenuPos({ x: e.clientX, y: e.clientY });
                  }
                }}
                type="button"
                disabled={hasMultipleCameras || hasNoFiles || isAnalyzing}
                title={hasMultipleCameras ? `Mixed Sensors: ${selectedCameras?.join(', ')}` : "Left click: Standard Stack • Right click: Configure Settings"}
              >
                {hasMultipleCameras ? "⚠️ Stacking requires matching cameras" : "Stack Frames"}
              </button>
            )}
            {contextMenuPos && (
              <StackingConfigContextMenu
                position={contextMenuPos}
                onClose={() => setContextMenuPos(null)}
                onStackWithConfig={(config: StackingConfig) => {
                  if (onProcess) {
                    onProcess();
                  }
                }}
              />
            )}
            {isProcessing && onCancel && (
              <button
                className="btn btn--danger"
                onClick={onCancel}
                type="button"
              >
                Cancel
              </button>
            )}
            <button
              className="btn"
              onClick={handleOpenSiril}
              type="button"
            >
              Open Siril
            </button>
          </div>
        </div>
      </div>

      <div className="image-details-manager__summary">
        <div className="summary-row">
          <strong>Target:</strong> {selectedTarget ?? '—'}
        </div>
        {commonName && (
          <div className="summary-row">
            <strong>Name:</strong> {commonName}
          </div>
        )}
        {catalogId && (
          <div className="summary-row">
            <strong>Catalog ID:</strong> {catalogId}
          </div>
        )}
      </div>
    </div>
  );
};
