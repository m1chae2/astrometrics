import React, { useState } from 'react';
import '../imageProcessingDisplay.css';
import { Button } from '../../common/components/Button';

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
  /** Callback to follow the spectrum of each star across the raw frames (stage two of spectral analysis). */
  onAnalyzeTemporalVariation?: () => void;
  /** Whether analysis is currently in progress. */
  isAnalyzing?: boolean;
  /** Whether multiple filter types are selected. */
  hasMultipleFilters?: boolean;
  /** List of filters currently selected. */
  selectedFilters?: string[];
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
  onProcess,
  onCancel,
  onAddImages,
  onAddTarget,
  isProcessing,
  hasMultipleCameras,
  selectedCameras,
  hasNoFiles,
  onAnalyze,
  onAnalyzeTemporalVariation,
  isAnalyzing,
  hasMultipleFilters,
  selectedFilters,
  onOpenSiril,
  onReindex,
  onSetStackedProperty,
}) => {

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
              <Button
                onClick={() => {
                  if (!isAnalyzing && !isProcessing) {
                    onAnalyze();
                  }
                }}
                disabled={hasNoFiles || isProcessing}
                title="Analyze Target"
              >
                Analyze Target
              </Button>
            )}

            {onAnalyzeTemporalVariation && !isAnalyzing && (
              <Button
                onClick={() => {
                  if (!isAnalyzing && !isProcessing) {
                    onAnalyzeTemporalVariation();
                  }
                }}
                disabled={isProcessing}
                title="Follow how each star's spectrum changes across the raw frames. Analyze the master stack first."
              >
                Analyze Temporal Variation
              </Button>
            )}

            {isAnalyzing && onCancel && (
              <Button
                variant="danger"
                onClick={onCancel}
              >
                Cancel
              </Button>
            )}

            {onProcess && !isProcessing && (
              <Button
                onClick={onProcess}
                disabled={hasMultipleCameras || hasNoFiles || isAnalyzing}
                title={hasMultipleCameras ? `Mixed Sensors: ${selectedCameras?.join(', ')}` : "Stack the frames"}
              >
                {hasMultipleCameras ? "⚠️ Stacking requires matching cameras" : "Stack Frames"}
              </Button>
            )}
            {isProcessing && onCancel && (
              <Button
                variant="danger"
                onClick={onCancel}
              >
                Cancel
              </Button>
            )}
            <Button
              onClick={handleOpenSiril}
            >
              Open Siril
            </Button>
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
