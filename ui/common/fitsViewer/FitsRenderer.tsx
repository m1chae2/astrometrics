/**
 * @file FitsRenderer.tsx
 * @description Renders FITS format and standard bitmap images onto an interactive HTML5 canvas.
 * Puts the viewer together: the loader and the canvas drawer, zoom and pan, the view placement
 * (`useViewPlacement`), the star overlay (`useStarOverlay`, `StarLabelLayer`), click-to-measure
 * (`useStarMeasurement`, `ProfileMarkerLayer`, `StarProfilePanel`) and the Detections overlay
 * (`useDetectionsOverlay`).
 */
import React, { useState, useRef, useCallback, useImperativeHandle, forwardRef } from 'react';
import { useFitsLoader } from './hooks/useFitsLoader';
import { useCanvasInteraction } from './hooks/useCanvasInteraction';
import { useCanvasDrawer } from './hooks/useCanvasDrawer';
import { usePreviewBitmap } from './hooks/usePreviewBitmap';
import { useFrameTurn } from './hooks/useFrameTurn';
import { useViewPlacement } from './hooks/useViewPlacement';
import type { FocusStar, RestoreView } from './hooks/useViewPlacement';
import { useStarOverlay } from './hooks/useStarOverlay';
import { useStarMeasurement } from './hooks/useStarMeasurement';
import { useDetectionsOverlay } from './hooks/useDetectionsOverlay';
import { PixelReadout } from './PixelReadout';
import { StarLabelLayer } from './StarLabelLayer';
import { ProfileMarkerLayer } from './ProfileMarkerLayer';
import { StarProfilePanel, SET_DISPLAY_TO_LINEAR_MESSAGE } from './StarProfilePanel';
import type { SavedViewport } from '../utils/imageViewStore';
import { describeTurn, turnedSize } from './displayTurn';
import type { QuarterTurn } from './displayTurn';
import { Badge } from '../components/Badge';
import { canOpenInAstronomyManager } from './utils/starNames';
import { AstrometryOverlayStar } from '../services/astronomyService';
import { Skeleton, UpdatingBar } from '../components/Skeleton';
import { StretchParameters } from '../types/backendTypes';
import { findStarNearPointer } from './utils/starOverlayLayout';

/** Most the pointer may move, in screen pixels, between press and release for the gesture to count as a click and not a pan. */
export const STAR_CLICK_MOVE_THRESHOLD_PX = 4;

/** Props for FitsRenderer: the image, how to draw it, its overlays and the view requests. */
export interface FitsRendererProps {
  imageUrl: string | null;
  imageBlob: Blob | null;
  frameInfo: any | null;
  loading: boolean;
  error: string | null;
  selectedTarget: string | null;
  autoPanTrigger: number;
  disableStretch?: boolean;
  stretch?: boolean;
  /** Pixel values drawn black and white when `stretch` is false (default: the file's own range). */
  displayRange?: readonly [number, number];
  /** The library's automatic stretch for a FITS image, sent by the backend; without it a stretched view is linear. */
  stretchParameters?: StretchParameters | null;
  overlayStars?: AstrometryOverlayStar[];
  showOverlay?: boolean;
  onStarClick?: (star: AstrometryOverlayStar) => void;
  selectedStarId?: string | null;
  /**
   * A small stretched picture (JPEG) of the same image. It is drawn at once while the
   * full FITS image is read and decoded, then replaced by it without moving the view.
   * Overlays that need FITS pixel positions wait for the full image.
   */
  previewBlob?: Blob | null;
  /** When true, this viewer keeps pictures decoded ahead of time (see `prefetchDecode`). */
  acceptPrefetch?: boolean;
  /** True while the Detections overlay is on: a marker at every star the astrometry run's star finder detects. */
  showDetections?: boolean;
  /** Finds the file path of the image on screen, so the backend can measure that file. Called for each measurement. */
  resolveMeasurePath?: () => Promise<string | null>;
  /** Shown in the star profile panel in place of a measurement when the image holds no pixel values (default: ask for Linear). */
  noPixelDataMessage?: string;
  /** A sentence about the picture shown in the star profile panel, for example that it is already stretched. */
  starProfilePictureNote?: string;
  /**
   * True to show each frame turned by the quarter turn that makes it match the target's stack (display
   * only), so frames from both sides of a meridian flip look the same way round. The backend decides the
   * turn for the file `resolveMeasurePath` names. Leave it out to show every frame as it is stored.
   */
  matchStackTurn?: boolean;
  /**
   * A star to put at the center of the view each time a picture is drawn. The place is a position of the
   * file, so the viewer finds it on the turned or redrawn picture. The caller gives the star of the
   * file that is on screen.
   */
  focusStar?: FocusStar | null;
  /**
   * A view to put back, for example when the person returns to this screen. It is applied once for each key,
   * as soon as a picture is drawn, and the picture stays hidden until then so the fitted view does not show first.
   */
  restoreView?: RestoreView | null;
  /** Called after the view changes (zoom or pan) once a picture is drawn, with where the view is now. */
  onViewChange?: (view: SavedViewport) => void;
  /** Called with true when the view is at the smallest zoom (the fit), and with false otherwise. */
  onMinimumZoomChange?: (atMinimum: boolean) => void;
}

/** The zoom controls FitsRenderer offers through its ref. */
export interface FitsRendererHandle {
  zoomIn: () => void;
  zoomOut: () => void;
  zoomToFit: () => void;
  zoomToScale: (val: number) => void;
}

/**
 * Component to render FITS images with canvas interaction.
 * Interaction methods exposed via ref.
 */
const FitsRendererInternal = forwardRef<FitsRendererHandle, FitsRendererProps>((props, ref) => {
  const {
    imageUrl,
    imageBlob,
    frameInfo,
    loading: parentLoading,
    error: parentError,
    autoPanTrigger,
    disableStretch,
    displayRange,
    stretchParameters,
    overlayStars: overlayStarsAsSent,
    showOverlay,
    onStarClick,
    selectedStarId,
    selectedTarget,
    previewBlob = null,
    acceptPrefetch = false,
    showDetections = false,
    resolveMeasurePath,
    noPixelDataMessage = SET_DISPLAY_TO_LINEAR_MESSAGE,
    starProfilePictureNote,
    matchStackTurn = false,
    focusStar = null,
    restoreView = null,
    onViewChange,
    onMinimumZoomChange,
  } = props;

  // Where the pointer went down, to tell a click from a pan drag.
  const pressStartRef = useRef<{ x: number; y: number; starId: string | null } | null>(null);

  // Refs
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [drawnSize, setDrawnSize] = useState({ w: 0, h: 0 });

  const handleSetDrawnSize = useCallback((size: { w: number; h: number }) => {
    setDrawnSize((prev) => (prev.w === size.w && prev.h === size.h ? prev : size));
  }, []);

  // Hooks
  const { parsedData, bitmap, workerRef, phase, errorMessage, loadedKey } = useFitsLoader(
    imageUrl, imageBlob, undefined, acceptPrefetch
  );
  const previewBitmap = usePreviewBitmap(previewBlob);

  // True while the next change of drawn size is the preview being replaced by the full image.
  const keepViewOnResizeRef = useRef(false);

  const view = useCanvasInteraction(containerRef, drawnSize, undefined, keepViewOnResizeRef);
  const { zoom, panX, panY, zoomBy, zoomToFit, zoomToScale, onPointerDown, onPointerMove, onPointerUp } = view;

  const currentKey = imageBlob ?? imageUrl;
  // The full image is ready to draw only when it was made from the file now asked for.
  const mainReady = loadedKey !== null && loadedKey === currentKey && Boolean(parsedData || bitmap);
  const mainObject = mainReady ? (parsedData ?? bitmap) : null;

  // A raw frame is shown turned to match the stack; the file itself is never changed. The backend
  // decides the turn of the file behind the picture, and the picture waits for it.
  const frameTurn = useFrameTurn(currentKey, selectedTarget, resolveMeasurePath, matchStackTurn);
  const turn: QuarterTurn = frameTurn.turn;
  // The size of the picture as shown: the file's width and height, swapped for a quarter turn.
  const shownSize = parsedData ? turnedSize(parsedData.w, parsedData.h, turn) : { w: 0, h: 0 };

  // Where the view sits on each newly drawn picture, and where it is reported to be.
  const {
    handleDrawn, mainDrawn, previewDrawn, firstFrameDrawn, restoreHidesPicture, appliedRestoreKey,
  } = useViewPlacement({
    currentKey, selectedTarget, mainObject, previewBitmap, previewAllowed: !mainReady, parsedData, workerRef, turn,
    shownSize, drawnSize, containerRef, view, keepViewOnResizeRef, focusStar, restoreView, onViewChange,
    onMinimumZoomChange, autoPanTrigger,
  });

  // Disable automatic stretching for stacked images.
  const isStacked = disableStretch ?? (
    (imageUrl?.toLowerCase().includes('stacked') ?? false) ||
    (frameInfo?.objectId?.toLowerCase().includes('stacked') ?? false)
  );

  const effectiveStretch = props.stretch ?? !isStacked;

  // Automatically draw with AutoStretch (handled in worker for FITS)
  useCanvasDrawer(
    canvasRef, workerRef, mainReady && frameTurn.ready ? parsedData : null,
    frameTurn.ready ? (mainReady ? bitmap : previewBitmap) : null,
    handleSetDrawnSize, effectiveStretch, displayRange, stretchParameters, handleDrawn, turn
  );

  // Expose methods via REF
  useImperativeHandle(ref, () => ({
    zoomIn: () => zoomBy(1.2),
    zoomOut: () => zoomBy(0.8),
    zoomToFit: () => zoomToFit(),
    zoomToScale: (v: number) => zoomToScale(v)
  }));

  const effectiveZoom = zoom && zoom > 0 ? zoom : 1;
  const containerW = containerRef.current?.clientWidth || (drawnSize.w * effectiveZoom);
  const containerH = containerRef.current?.clientHeight || (drawnSize.h * effectiveZoom);

  const {
    overlayStars, overlayVisible, scaleX, scaleY, screenTransform, labeledIds, markerCanvasRef,
    hoveredStarId, setHoveredStarId, hoverPoint, hoveredStar, handleHoverMove, clearHover,
  } = useStarOverlay({
    overlayStarsAsSent, showOverlay: Boolean(showOverlay), mainDrawn, selectedStarId, turn, parsedData, bitmap,
    drawnSize, effectiveZoom, panX, panY, containerW, containerH,
  });

  // Click-to-measure and the star profile panel.
  const {
    starProfile, setProfileView, measureStarAt, profileMarker, profileStarName, profileStarIdentity,
    openProfileStarInManager, profilePanelOpen,
  } = useStarMeasurement({
    resolveMeasurePath, currentKey, workerRef, parsedData, turn, shownSize, drawnSize, effectiveZoom, panX, panY,
    mainDrawn, firstFrameDrawn, previewDrawn, noPixelDataMessage, restoreView, appliedRestoreKey, showOverlay,
    overlayStarsAsSent, onStarClick,
  });

  // The Detections overlay: fetched while it is on, drawn with the same placement as the star profile marker.
  const { detections, detectionPaths, detectionsNote, frameStarWidthPx } = useDetectionsOverlay({
    showDetections, profilePanelOpen, mainDrawn, parsedData, workerRef, currentKey, resolveMeasurePath,
    noPixelDataMessage, turn, shownSize, drawnSize, effectiveZoom, panX, panY, containerW, containerH,
  });

  /**
   * Opens the star under the pointer when the press was a click and not a pan.
   * The star is a label badge under the pointer, or else the nearest circle.
   *
   * @param event Pointer up event on the viewer container.
   */
  const handleStarRelease = (event: React.PointerEvent<HTMLDivElement>) => {
    const start = pressStartRef.current;
    pressStartRef.current = null;
    if (!start) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const moved = Math.hypot(event.clientX - start.x, event.clientY - start.y);
    const badgeStarId = start.starId
      ?? (event.target as Element).closest?.('[data-star-id]')?.getAttribute('data-star-id');
    let star = badgeStarId && overlayStars ? overlayStars.find((item) => item.id === badgeStarId) : undefined;
    if (!star && overlayVisible && overlayStars) {
      // Only a star the Astronomy Manager can open matters here, so look among those first.
      star = findStarNearPointer(
        overlayStars.filter(canOpenInAstronomyManager), screenTransform, event.clientX - rect.left, event.clientY - rect.top
      ) ?? undefined;
    }
    // A named star the Astronomy Manager can open still opens there; any other press measures a star.
    if (star && onStarClick && overlayVisible && canOpenInAstronomyManager(star)) {
      if (moved < STAR_CLICK_MOVE_THRESHOLD_PX) onStarClick(star);
      return;
    }
    // A press that did not move is a click and measures a star; a drag only panned.
    if (moved < STAR_CLICK_MOVE_THRESHOLD_PX) {
      void measureStarAt(event.clientX - rect.left, event.clientY - rect.top);
    }
  };

  // An explicit error wins; otherwise the spinner stays until the first real draw
  // (the preview or the full image), whatever the loader is doing.
  const effectiveError = parentError || (phase === 'error' ? errorMessage : null);
  const effectiveLoading = !effectiveError && (!firstFrameDrawn || (parentLoading && !previewDrawn));

  return (
    <div className="fits-renderer">
      <div
        ref={containerRef}
        className={`fits-renderer__canvas-container${restoreHidesPicture ? ' fits-renderer__canvas-container--restoring' : ''}`}
        onPointerDown={(event) => {
          // The star group under the press is read now: pointer capture makes the release target the container.
          const pressedStarId = (event.target as Element).closest?.('[data-star-id]')?.getAttribute('data-star-id') ?? null;
          pressStartRef.current = { x: event.clientX, y: event.clientY, starId: pressedStarId };
          onPointerDown(event);
        }}
        onPointerMove={(event) => { onPointerMove(event); handleHoverMove(event); }}
        onPointerUp={(event) => { onPointerUp(event); handleStarRelease(event); }}
        onPointerLeave={(event) => { pressStartRef.current = null; onPointerUp(event); clearHover(); }}
        style={{ touchAction: 'none', cursor: hoveredStarId ? 'pointer' : 'crosshair' }}
      >
        <canvas
          ref={canvasRef}
          className={`fits-renderer__canvas ${firstFrameDrawn ? '' : 'fits-renderer__canvas--hidden'}`}
        />
        {overlayVisible && (
          <canvas
            ref={markerCanvasRef}
            data-overlay-layer="true"
            className="fits-renderer__marker-layer"
            style={{ width: containerW, height: containerH }}
          />
        )}
        {overlayVisible && overlayStars && (
          <StarLabelLayer
            stars={overlayStars}
            labeledIds={labeledIds}
            selectedStarId={selectedStarId}
            hoveredStarId={hoveredStarId}
            onHoverChange={setHoveredStarId}
            onStarClick={onStarClick}
            scaleX={scaleX}
            scaleY={scaleY}
            panX={panX}
            panY={panY}
            containerW={containerW}
          />
        )}
        {showDetections && detectionPaths && (
          <svg className={`fits-renderer__detections${detectionPaths.mode === 'dots' ? ' fits-renderer__detections--dots' : ''}`} data-mode={detectionPaths.mode} data-overlay-layer="true" data-testid="detections-layer" aria-hidden="true">
            <path className="fits-renderer__detection" data-testid="detections-single" d={detectionPaths.circles} style={detectionPaths.mode === 'dots' ? { fillOpacity: detectionPaths.dotOpacity } : undefined} />
            <path className="fits-renderer__detection fits-renderer__detection--merged" data-testid="detections-merged" d={detectionPaths.diamonds} style={detectionPaths.mode === 'dots' ? { fillOpacity: detectionPaths.mergedDotOpacity } : undefined} />
          </svg>
        )}
        {profileMarker && <ProfileMarkerLayer marker={profileMarker} />}
        {hoveredStar && hoverPoint && !labeledIds.has(hoveredStar.id) && (
          <div
            className="fits-renderer__star-tooltip"
            role="tooltip"
            style={{ left: hoverPoint.x + 12, top: hoverPoint.y + 12 }}
          >
            {onStarClick && canOpenInAstronomyManager(hoveredStar) ? `Open ${hoveredStar.name} in the Astronomy Manager` : hoveredStar.name}
          </div>
        )}
      </div>

      {detectionsNote && (
        <div
          className={`fits-renderer__detections-note${detections.status === 'error' ? ' fits-renderer__detections-note--error' : ''}`}
          role="status"
          data-testid="detections-note"
          title="Circles or small dots are single stars; diamonds or larger dots are stars reported twice and merged."
        >
          {detectionsNote}
          {detections.status === 'ready' && detections.measurement.merged_count > 0 && ` (${detections.measurement.merged_count} merged, shown as ${detectionPaths?.mode === 'dots' ? 'larger dots' : 'diamonds'})`}
        </div>
      )}

      <PixelReadout
        containerRef={containerRef}
        workerRef={workerRef}
        parsedData={mainDrawn ? parsedData : null}
        drawnSize={drawnSize}
        zoom={effectiveZoom}
        panX={panX}
        panY={panY}
        turn={turn}
      />

      {mainDrawn && turn !== 0 && (
        <div className="fits-renderer__turn-note" data-testid="turn-note">
          <Badge
            opaque
            tone="info"
            title="This frame is stored the other way round from the stack, so the viewer draws it turned to match. The file is not changed, and pixel positions are the file's own."
          >
            {describeTurn(turn)}
          </Badge>
        </div>
      )}

      <StarProfilePanel
        state={starProfile.state}
        cutAngleDegrees={starProfile.cutAngleDegrees}
        onCutAngleChange={starProfile.setCutAngle}
        cutLengthPx={starProfile.cutLengthPx}
        onCutLengthChange={starProfile.setCutLength}
        onViewChange={setProfileView}
        followNote={starProfile.followNote}
        onClose={starProfile.close}
        pictureNote={starProfilePictureNote}
        starName={profileStarName}
        identity={profileStarIdentity}
        frameWidthPx={frameStarWidthPx}
        onOpenInManager={openProfileStarInManager}
      />

      {effectiveLoading && (
        <div className="fits-renderer__loading-overlay">
          <Skeleton variant="block" label="Loading the image" />
        </div>
      )}

      {/* The small preview is on screen while the full FITS image is still being made. */}
      {!effectiveError && previewDrawn && !mainDrawn && <UpdatingBar label="Loading the full image" />}

      {effectiveError && (
        <div className="fits-renderer__status error">
          {effectiveError}
        </div>
      )}
    </div>
  );
});

FitsRendererInternal.displayName = 'FitsRenderer';

export const FitsRenderer = React.memo(FitsRendererInternal, (prevProps, nextProps) => {
  return (
    prevProps.imageUrl === nextProps.imageUrl &&
    prevProps.imageBlob === nextProps.imageBlob &&
    prevProps.loading === nextProps.loading &&
    prevProps.error === nextProps.error &&
    prevProps.selectedTarget === nextProps.selectedTarget &&
    prevProps.autoPanTrigger === nextProps.autoPanTrigger &&
    prevProps.disableStretch === nextProps.disableStretch &&
    prevProps.stretch === nextProps.stretch &&
    prevProps.displayRange === nextProps.displayRange &&
    prevProps.stretchParameters === nextProps.stretchParameters &&
    prevProps.showOverlay === nextProps.showOverlay &&
    prevProps.overlayStars === nextProps.overlayStars &&
    prevProps.selectedStarId === nextProps.selectedStarId &&
    prevProps.previewBlob === nextProps.previewBlob &&
    prevProps.acceptPrefetch === nextProps.acceptPrefetch &&
    prevProps.showDetections === nextProps.showDetections &&
    prevProps.resolveMeasurePath === nextProps.resolveMeasurePath &&
    prevProps.noPixelDataMessage === nextProps.noPixelDataMessage &&
    prevProps.starProfilePictureNote === nextProps.starProfilePictureNote &&
    prevProps.matchStackTurn === nextProps.matchStackTurn &&
    prevProps.restoreView === nextProps.restoreView
  );
});
