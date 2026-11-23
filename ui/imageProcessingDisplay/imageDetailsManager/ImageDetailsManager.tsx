import React, { useState } from 'react';
import '../imageProcessingDisplay.css';
import { Button } from '../../common/components/Button';
import { SplitButton, type SplitButtonOption } from '../../common/components/SplitButton';
import {
  ANALYZE_TARGET_TOOLTIP,
  describeStageAvailability,
  type AnalysisSelectionInput,
  type AnalysisStage,
} from '../utils/analysisSelection';
import { usePostProcessTarget } from '../hooks/usePostProcessTarget';
import { useCheckFrames } from '../hooks/useCheckFrames';
import { StackCheckReport } from '../components/StackCheckReport';
import type { StackKind } from '../../common/services/imaging/stackReviewService';

/** The id of the one entry in the Stack Frames menu. */
const POST_PROCESS_ID = 'post-process';

/** The id of the Check Frames entry in the Stack Frames menu. */
const CHECK_FRAMES_ID = 'check-frames';

/** The stages in the Analyze Target menu. The short text is only the tooltip of an available stage. */
const ANALYSIS_STAGE_MENU: { id: AnalysisStage; label: string; detail: string }[] = [
  { id: 'astrometry', label: 'Astrometry', detail: 'Solve the stack to find where the stars are' },
  { id: 'photometry', label: 'Photometry', detail: 'Runs astrometry first when needed' },
  { id: 'spectroscopy', label: 'Spectroscopy', detail: 'Runs astrometry first when needed' },
  {
    id: 'spectral_variation',
    label: 'Spectral Variation',
    detail: "Follow how each star's spectrum changes across the raw frames. Runs spectroscopy first when needed",
  },
];

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
  /** Gives the file paths the main Stack Frames click would send. Check Frames checks the same frames. */
  framesToStack?: () => string[];
  /** Callback to cancel a processing operation. */
  onCancel?: () => void;
  /** Callback to cancel the running analysis; the Cancel shown while analyzing calls it (else `onCancel`). */
  onCancelAnalysis?: () => void;
  /** Callback to add new images to the target. */
  onAddImages?: () => void;
  /** Callback to manually add a new target entry. */
  onAddTarget?: () => void;
  /** Whether a processing operation is currently in progress. */
  isProcessing?: boolean;
  /** Why the last stacking or analysis job failed, shown in the panel; `null` or absent for none. */
  jobError?: string | null;
  /** Whether multiple camera types are selected. */
  hasMultipleCameras?: boolean;
  /** List of camera names currently selected. */
  selectedCameras?: string[];
  /** Whether the frames come from more than one optic (focal length). */
  hasMultipleOptics?: boolean;
  /** The focal lengths in mm of the frames to stack. */
  selectedOptics?: number[];
  /** Whether no files are selected/present for processing. */
  hasNoFiles?: boolean;
  /** Callback to start analysis. */
  onAnalyze?: () => void;
  /** When set, the main Analyze Target click is off and this text is its tooltip. */
  analyzeDisabledReason?: string;
  /** What the menu needs to know about stacks and the selected file, to turn stages on or off. */
  analysisSelectionInput?: AnalysisSelectionInput;
  /** Callback to run one named stage from the Analyze Target menu. */
  onAnalyzeStage?: (stage: AnalysisStage) => void;
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
  /** Called with a stack's path when its remade picture is on display, so the viewer can open it. */
  onShowPicture?: (stackPath: string) => void;
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
  framesToStack,
  onCancel,
  onCancelAnalysis,
  onAddImages,
  onAddTarget,
  isProcessing,
  jobError,
  hasMultipleCameras,
  selectedCameras,
  hasMultipleOptics,
  selectedOptics,
  hasNoFiles,
  onAnalyze,
  analyzeDisabledReason,
  analysisSelectionInput,
  onAnalyzeStage,
  isAnalyzing,
  hasMultipleFilters,
  onOpenSiril,
  onReindex,
  onSetStackedProperty,
  onShowPicture,
}) => {
  const postProcess = usePostProcessTarget(selectedTarget, onShowPicture);
  const checkFrames = useCheckFrames(selectedTarget);

  // Check Frames follows the same rules as the main Stack Frames click.
  const checkFramesBlockedReason = hasMultipleCameras
    ? 'Frames from more than one camera cannot be stacked. Pick one camera.'
    : hasMultipleOptics
      ? 'Frames from more than one optic cannot be stacked. Pick one optic.'
      : hasNoFiles
        ? 'There are no frames to check.'
        : undefined;

  // Post Process Target remakes the picture of each stack the target has, imaging first.
  const stacksToRemake: StackKind[] = [];
  if (analysisSelectionInput?.stackedImage) stacksToRemake.push('imaging');
  if (analysisSelectionInput?.stackedSpectralTarget) stacksToRemake.push('spectral');
  const stackOptions: SplitButtonOption[] = [
    {
      id: POST_PROCESS_ID,
      label: 'Post Process Target',
      disabled: stacksToRemake.length === 0,
      title: stacksToRemake.length === 0
        ? 'This target has no stack yet. Stack the frames first.'
        : 'Make the picture again from the existing stack, without stacking. Uses your current settings.',
    },
    {
      id: CHECK_FRAMES_ID,
      label: 'Check Frames',
      disabled: Boolean(checkFramesBlockedReason) || checkFrames.busy || !framesToStack,
      title: checkFramesBlockedReason
        ?? 'Show which frames stacking would set aside and which calibration frames it would use, without stacking.',
    },
  ];

  /** Helper to trigger Open Siril event. */
  const handleOpenSiril = (): void => {
    if (onOpenSiril) {
      onOpenSiril();
    }
  };

  // Each menu stage is on or off depending on the stacks and the selected file.
  const stageAvailability = analysisSelectionInput ? describeStageAvailability(analysisSelectionInput) : null;
  const stageOptions: SplitButtonOption[] = ANALYSIS_STAGE_MENU.map((stage) => {
    const availability = stageAvailability?.[stage.id];
    return {
      id: stage.id,
      label: stage.label,
      disabled: !availability || !availability.enabled,
      title: availability && !availability.enabled ? availability.reason : stage.detail,
    };
  });

  return (
    <div className="image-details-manager">
      <div className="image-details-manager__controls-frame">
        <div className="image-details-manager__controls">
          <div className="image-details-manager__toolbar">
            {onAnalyze && !isAnalyzing && (
              <SplitButton
                label="Analyze Target"
                menuLabel="Choose an analysis stage"
                title={analyzeDisabledReason ?? ANALYZE_TARGET_TOOLTIP}
                fill
                disabled={hasNoFiles || isProcessing || Boolean(analyzeDisabledReason)}
                onClick={() => {
                  if (!isAnalyzing && !isProcessing) {
                    onAnalyze();
                  }
                }}
                options={stageOptions}
                onSelect={(stageId) => {
                  if (!isAnalyzing && !isProcessing && onAnalyzeStage) {
                    onAnalyzeStage(stageId as AnalysisStage);
                  }
                }}
              />
            )}

            {isAnalyzing && (onCancelAnalysis ?? onCancel) && (
              <Button
                variant="danger"
                onClick={onCancelAnalysis ?? onCancel}
              >
                Cancel
              </Button>
            )}

            {onProcess && !isProcessing && !postProcess.busy && (
              <SplitButton
                label={hasMultipleCameras
                  ? "Stacking requires matching cameras"
                  : hasMultipleOptics ? "Pick one optic to stack" : "Stack Frames"}
                menuLabel="Choose a stacking action"
                title={hasMultipleCameras
                  ? `Mixed Sensors: ${selectedCameras?.join(', ')}`
                  : hasMultipleOptics
                    ? `These frames come from more than one optic (${selectedOptics?.join(', ')} mm). Pick one optic to stack.`
                    : "Stack the frames"}
                fill
                disabled={hasMultipleCameras || hasMultipleOptics || hasNoFiles || isAnalyzing}
                onClick={onProcess}
                options={stackOptions}
                onSelect={(entryId) => {
                  if (entryId === POST_PROCESS_ID && !isAnalyzing && !isProcessing) {
                    postProcess.start(stacksToRemake);
                  }
                  if (entryId === CHECK_FRAMES_ID && !isAnalyzing && !isProcessing && framesToStack) {
                    checkFrames.start(framesToStack());
                  }
                }}
              />
            )}
            {/* The picture remake shows a busy button and a Cancel button (processing:cancel stops it). */}
            {onProcess && postProcess.busy && (
              <SplitButton
                label="Post Processing..."
                menuLabel="Choose a stacking action"
                title="Making the picture again. This can take a minute."
                fill
                busy
                onClick={() => undefined}
                options={stackOptions}
                onSelect={() => undefined}
              />
            )}
            {onProcess && postProcess.busy && (
              <Button variant="danger" onClick={postProcess.cancel}>
                Cancel
              </Button>
            )}
            {jobError && (
              <div className="stack-summary--error" role="alert">
                The job failed: {jobError}
              </div>
            )}
            {postProcess.errorMessage && (
              <div className="stack-summary--error" role="alert">
                Could not make the picture: {postProcess.errorMessage}
              </div>
            )}
            {checkFrames.busy && (
              <div role="status">Checking the frames. This reads every frame, so it can take a minute.</div>
            )}
            {checkFrames.errorMessage && (
              <div className="stack-summary--error" role="alert">
                Could not check the frames: {checkFrames.errorMessage}
              </div>
            )}
            {checkFrames.prediction && <StackCheckReport prediction={checkFrames.prediction} />}
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
