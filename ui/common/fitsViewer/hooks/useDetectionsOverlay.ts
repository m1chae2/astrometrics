/**
 * @file useDetectionsOverlay.ts
 * @description The Detections overlay of the FITS viewer: a marker at every star the astrometry
 * run's star finder detects. It fetches the detections while the overlay (or the star profile
 * panel) is on, builds the marker paths for the current view, and writes the note under the
 * picture. The star profile panel reads the frame's typical star width from the same answer.
 */

import { useEffect, useMemo, useState, type RefObject } from 'react';
import type { ParsedFitsData } from './useFitsLoader';
import { useDetections, type DetectionsState } from './useDetections';
import { askWorkerForPixel } from '../utils/pixelQuery';
import { buildDetectionPaths, type DetectionPaths } from '../utils/detectionMarkers';
import { rowOrderProbeCell } from '../displayTurn';
import type { QuarterTurn } from '../displayTurn';

/** What `useDetectionsOverlay` needs from the viewer. */
export interface DetectionsOverlayInput {
  /** True while the Detections overlay is on. */
  showDetections: boolean;
  /** True while the star profile panel shows a measurement (it needs the frame's star width). */
  profilePanelOpen: boolean;
  /** True once the full image is on the canvas. */
  mainDrawn: boolean;
  /** The decoded FITS data, or null for a bitmap. */
  parsedData: ParsedFitsData | null;
  /** The decoding worker, which tells the file's row order. */
  workerRef: RefObject<Worker | null>;
  /** The image asked for now. */
  currentKey: unknown;
  /** Finds the file path of the image on screen. */
  resolveMeasurePath: (() => Promise<string | null>) | undefined;
  /** Shown in place of the count when the image holds no pixel values. */
  noPixelDataMessage: string;
  /** The quarter turn the picture is shown with. */
  turn: QuarterTurn;
  /** The size of the picture as shown. */
  shownSize: { w: number; h: number };
  /** The size the picture is drawn at on the canvas. */
  drawnSize: { w: number; h: number };
  /** The zoom, in screen pixels per drawn pixel (never 0). */
  effectiveZoom: number;
  /** The picture's offset on screen. */
  panX: number;
  /** The picture's offset on screen. */
  panY: number;
  /** The viewer's width on screen. */
  containerW: number;
  /** The viewer's height on screen. */
  containerH: number;
}

/** What `useDetectionsOverlay` gives back. */
export interface DetectionsOverlay {
  /** The detections as fetched. */
  detections: DetectionsState;
  /** The marker paths for the current view, or null when nothing is drawn. */
  detectionPaths: DetectionPaths | null;
  /** The note under the picture, or null. */
  detectionsNote: string | null;
  /** The frame's typical star width in pixels, for the star profile panel, or null. */
  frameStarWidthPx: number | null;
}

/**
 * Fetches the detections and builds their markers and note for the current view.
 *
 * @param input The overlay switch, the picture and the view (see `DetectionsOverlayInput`).
 * @returns The detections, their paths and the note (see `DetectionsOverlay`).
 */
export function useDetectionsOverlay(input: DetectionsOverlayInput): DetectionsOverlay {
  const {
    showDetections, profilePanelOpen, mainDrawn, parsedData, workerRef, currentKey, resolveMeasurePath,
    noPixelDataMessage, turn, shownSize, drawnSize, effectiveZoom, panX, panY, containerW, containerH,
  } = input;
  const detections = useDetections((showDetections || profilePanelOpen) && mainDrawn && parsedData !== null, currentKey, resolveMeasurePath);
  const frameStarWidthPx = detections.status === 'ready' ? detections.measurement.star_width_px ?? null : null;
  const [detectionsFirstRowAtTop, setDetectionsFirstRowAtTop] = useState<boolean | null>(null);
  useEffect(() => {
    const worker = workerRef.current;
    if (!showDetections || !mainDrawn || !parsedData || !worker) return;
    let cancelled = false;
    void askWorkerForPixel(worker, parsedData.imageId, rowOrderProbeCell(parsedData.w, parsedData.h, turn)).then((reading) => {
      if (!cancelled) setDetectionsFirstRowAtTop(reading ? reading.y === 1 : null);
    });
    return () => { cancelled = true; };
  }, [showDetections, mainDrawn, parsedData, workerRef, turn]);
  // Rebuilt only when the view or the detections change, not on every other re-render (hover, panel changes).
  const detectionPaths = useMemo(() => {
    if (!showDetections || detections.status !== 'ready' || detectionsFirstRowAtTop === null || !parsedData || !mainDrawn) return null;
    if (drawnSize.w <= 0 || drawnSize.h <= 0) return null;
    return buildDetectionPaths(detections.measurement.detections, {
      imageWidth: parsedData.w,
      imageHeight: parsedData.h,
      stepX: (drawnSize.w / shownSize.w) * effectiveZoom,
      stepY: (drawnSize.h / shownSize.h) * effectiveZoom,
      panX,
      panY,
      firstRowAtTop: detectionsFirstRowAtTop,
      turn,
      viewWidth: containerW,
      viewHeight: containerH,
      apertureRadiusPx: detections.measurement.aperture_radius_px,
    });
    // shownSize follows parsedData and turn, which are listed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detections, detectionsFirstRowAtTop, parsedData, mainDrawn, drawnSize.w, drawnSize.h, effectiveZoom, panX, panY, containerW, containerH, turn]);
  const detectionsNote: string | null = !showDetections ? null
    : mainDrawn && !parsedData ? noPixelDataMessage
    : detections.status === 'loading' ? 'Finding stars…'
      : detections.status === 'error' ? detections.message
        : detections.status === 'ready' ? `${detections.measurement.detection_count.toLocaleString('en-US')} detections${detectionPaths?.mode === 'dots' ? '. Zoom in to see the photometry circles' : ''}`
          : null;

  return { detections, detectionPaths, detectionsNote, frameStarWidthPx };
}
