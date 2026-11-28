/**
 * @file useStarMeasurement.ts
 * @description Click-to-measure in the FITS viewer. A click (or a saved view's star pick)
 * becomes a file pixel, the backend measures the star there, and the star profile panel shows
 * the result. A pinned star is found again on the next image. This hook also works out the
 * marker drawn on the measured star and the star's name and catalog record for the panel.
 */

import { useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from 'react';
import type { ParsedFitsData } from './useFitsLoader';
import { useStarProfile, type StarProfileController } from './useStarProfile';
import { useStarIdentity } from './useStarIdentity';
import type { RestoreView } from './useViewPlacement';
import type { StarProfileView } from '../StarProfilePanel';
import { askWorkerForPixel, pointerToFileCell } from '../utils/pixelQuery';
import type { FileCell } from '../utils/pixelQuery';
import { fitsPixelToTurnedPicture, rowOrderProbeCell, turnedSize, turnStep } from '../displayTurn';
import type { QuarterTurn } from '../displayTurn';
import { createPixelToSky } from '../wcs';
import { canOpenInAstronomyManager, findStarAt, nameStarAt } from '../utils/starNames';
import type { AstrometryOverlayStar } from '../../services/astronomyService';

/** How far from a click, in screen pixels, a measurement may look for the star's center. */
export const STAR_SEARCH_SCREEN_PX = 6;

/** Smallest search radius sent to the backend, in image pixels. */
export const STAR_SEARCH_MIN_PX = 3;

/** Largest search radius sent to the backend, in image pixels (the backend caps it at 50 too). */
export const STAR_SEARCH_MAX_PX = 50;

/** Radius, in screen pixels, of the small marker drawn when the profile does not look like a star. */
const NOT_A_STAR_MARKER_RADIUS_PX = 4;

/**
 * Turns the star search distance of a click (6 screen pixels) into image pixels at a zoom.
 *
 * @param screenPixelsPerFilePixel The zoom, in screen pixels per image pixel.
 * @returns The search radius, from 3 to 50 image pixels.
 */
export function searchRadiusAtScale(screenPixelsPerFilePixel: number): number {
  return Math.min(STAR_SEARCH_MAX_PX, Math.max(STAR_SEARCH_MIN_PX, STAR_SEARCH_SCREEN_PX / screenPixelsPerFilePixel));
}

/** The marker on the measured star, in screen pixels. */
export interface ProfileMarker {
  /** The star's center on screen, across. */
  screenX: number;
  /** The star's center on screen, down. */
  screenY: number;
  /** The ring's radius: half the fitted FWHM, or a small fixed size when no star was found. */
  radius: number;
  /** The line of the cut, when the panel shows the cut view. */
  cut: { x1: number; y1: number; x2: number; y2: number } | null;
  /** The two circles of the background ring, when the panel shows the ring view. */
  band: { inner: number; outer: number } | null;
  /** The neighbor stars left out of the ring, as circles of their mask radius. */
  neighbors: Array<{ x: number; y: number; radius: number }>;
  /** False when the profile does not look like a star. */
  starFound: boolean;
}

/** What `useStarMeasurement` needs from the viewer. */
export interface StarMeasurementInput {
  /** Finds the file path of the image on screen, so the backend can measure that file. */
  resolveMeasurePath: (() => Promise<string | null>) | undefined;
  /** The image asked for now. */
  currentKey: unknown;
  /** The decoding worker, which names the file pixel under a point. */
  workerRef: RefObject<Worker | null>;
  /** The decoded FITS data, or null for a bitmap. */
  parsedData: ParsedFitsData | null;
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
  /** True once the full image is on the canvas. */
  mainDrawn: boolean;
  /** True once anything is on the canvas. */
  firstFrameDrawn: boolean;
  /** True while the preview is on the canvas. */
  previewDrawn: boolean;
  /** Shown in place of a measurement when the image holds no pixel values. */
  noPixelDataMessage: string;
  /** A saved view; with `pickStar` it picks the star at its center once it is put back. */
  restoreView: RestoreView | null;
  /** The key of the last saved view put back. */
  appliedRestoreKey: string | null;
  /** True while the star overlay is on. */
  showOverlay: boolean | undefined;
  /** The overlay stars as the backend sent them (in file pixels). */
  overlayStarsAsSent: AstrometryOverlayStar[] | undefined;
  /** Opens a star in the Astronomy Manager. */
  onStarClick?: (star: AstrometryOverlayStar) => void;
}

/** What `useStarMeasurement` gives back. */
export interface StarMeasurement {
  /** The star profile panel's state and controls. */
  starProfile: StarProfileController;
  /** Tells the hook what the panel shows (the cut line or the ring), so the marker matches. */
  setProfileView: (view: StarProfileView | null) => void;
  /** Measures the star at a point of the viewer, in screen pixels. */
  measureStarAt: (pointerX: number, pointerY: number) => Promise<void>;
  /** The marker on the measured star, or null. */
  profileMarker: ProfileMarker | null;
  /** The measured star's name for the panel, or null. */
  profileStarName: ReturnType<typeof nameStarAt> | null;
  /** The measured star's catalog record, when the catalog names it. */
  profileStarIdentity: ReturnType<typeof useStarIdentity>;
  /** Opens the measured star in the Astronomy Manager, when it can be opened. */
  openProfileStarInManager: (() => void) | undefined;
  /** True while the panel shows a measurement. */
  profilePanelOpen: boolean;
}

/**
 * Runs click-to-measure and works out what the star profile panel and its marker show.
 *
 * @param input The picture, the view and the overlay (see `StarMeasurementInput`).
 * @returns The panel's controller, the measure action and the marker (see `StarMeasurement`).
 */
export function useStarMeasurement(input: StarMeasurementInput): StarMeasurement {
  const {
    resolveMeasurePath, currentKey, workerRef, parsedData, turn, shownSize, drawnSize, effectiveZoom, panX, panY,
    mainDrawn, firstFrameDrawn, previewDrawn, noPixelDataMessage, restoreView, appliedRestoreKey, showOverlay,
    overlayStarsAsSent, onStarClick,
  } = input;

  // Click-to-measure: the panel's state, and the measurements behind it.
  const starProfile = useStarProfile(resolveMeasurePath);
  // What the panel says the image should draw: the cut line, or the two circles of the ring view.
  const [profileView, setProfileView] = useState<StarProfileView | null>(null);
  const closeStarProfile = starProfile.close;
  // A different image: a pinned star is followed to it once it is drawn (below); with no pinned star there is nothing to show.
  const hasPin = starProfile.pin !== null;
  const hasPinRef = useRef(hasPin);
  useLayoutEffect(() => {
    hasPinRef.current = hasPin;
  });
  useEffect(() => {
    if (!hasPinRef.current) closeStarProfile();
  }, [currentKey, closeStarProfile]);

  /** The image the star profile was last measured or followed on, so each image is followed only once. */
  const followedKeyRef = useRef<unknown>(null);

  /**
   * Turns 6 screen pixels into image pixels at the current zoom.
   *
   * @param imageWidth Width of the picture as shown, in pixels.
   * @returns The search radius, from 3 to 50 image pixels.
   */
  const searchRadiusFor = (imageWidth: number): number => searchRadiusAtScale((drawnSize.w / imageWidth) * effectiveZoom);

  /**
   * Measures the star at one cell of the picture as shown and opens the star profile for it. A click and a
   * requested pick (`restoreView.pickStar`) both come here, so they open the same panel the same way.
   *
   * @param cell The cell of the picture as shown.
   * @param searchRadiusPx How far, in image pixels, the center may snap to a star.
   */
  const measureCell = async (cell: FileCell, searchRadiusPx: number) => {
    const worker = workerRef.current;
    if (!parsedData || !worker) return;
    // The reading names the file's own pixel; the probe (the picture's top-left base corner) tells the row order.
    const [reading, topRowReading] = await Promise.all([
      askWorkerForPixel(worker, parsedData.imageId, cell),
      askWorkerForPixel(worker, parsedData.imageId, rowOrderProbeCell(parsedData.w, parsedData.h, turn)),
    ]);
    if (!reading) return;
    followedKeyRef.current = currentKey;
    starProfile.measureAt({ x: reading.x, y: reading.y, firstRowAtTop: topRowReading?.y === 1, searchRadiusPx, turn });
  };

  /**
   * Measures the star at a point of the viewer. The point becomes a file pixel with the same steps as
   * the pixel readout, and the worker names the pixel, so a click and the readout agree. An image
   * without decoded pixel values cannot say which pixel was clicked, so the panel says so and
   * nothing is sent to the backend.
   *
   * @param pointerX Horizontal position inside the viewer, in screen pixels.
   * @param pointerY Vertical position inside the viewer, in screen pixels.
   */
  const measureStarAt = async (pointerX: number, pointerY: number) => {
    const worker = workerRef.current;
    if (!mainDrawn || !parsedData || !worker) {
      // While the image is still loading there is nothing to say yet.
      if (!mainDrawn && (!firstFrameDrawn || previewDrawn)) return;
      followedKeyRef.current = currentKey;
      starProfile.showUnavailable(noPixelDataMessage);
      return;
    }
    const cell = pointerToFileCell(pointerX, pointerY, parsedData, drawnSize, effectiveZoom, panX, panY, turn);
    if (!cell) return;
    // The center may snap to a star within about 6 screen pixels of the click, so the search radius follows the zoom.
    await measureCell(cell, searchRadiusFor(shownSize.w));
  };

  // A view put back with `pickStar` picks the star at its center once the file's pixels are drawn, as a click there would.
  const pickedRestoreKeyRef = useRef<string | null>(null);
  useEffect(() => {
    if (!restoreView?.pickStar || appliedRestoreKey !== restoreView.key || !mainDrawn || !parsedData) return;
    if (pickedRestoreKeyRef.current === restoreView.key) return;
    pickedRestoreKeyRef.current = restoreView.key;
    const shown = turnedSize(parsedData.w, parsedData.h, turn);
    const cell: FileCell = {
      column: Math.min(shown.w - 1, Math.max(0, Math.floor(restoreView.view.x * shown.w))),
      rowFromTop: Math.min(shown.h - 1, Math.max(0, Math.floor(restoreView.view.y * shown.h))),
    };
    // The zoom the view was asked for, which the state may not show yet.
    const screenPixelsPerFilePixel = restoreView.scale ?? (drawnSize.w / shown.w) * effectiveZoom;
    void measureCell(cell, searchRadiusAtScale(screenPixelsPerFilePixel));
    // measureCell reads the newest image and settings when it runs; the pick is keyed by the request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [restoreView, appliedRestoreKey, mainDrawn, parsedData]);

  // When another image is drawn while a star is pinned, find the star on it (once per image).
  const followProfile = starProfile.follow;
  const profileOpen = starProfile.state.status !== 'closed';
  useEffect(() => {
    if (!profileOpen || !hasPinRef.current || !mainDrawn) return;
    if (followedKeyRef.current === currentKey) return;
    followedKeyRef.current = currentKey;
    const worker = workerRef.current;
    if (!parsedData || !worker) {
      // The pinned star stays pinned; this picture just cannot be measured.
      starProfile.showUnavailable(noPixelDataMessage);
      return;
    }
    const firstCell = rowOrderProbeCell(parsedData.w, parsedData.h, turn);
    void askWorkerForPixel(worker, parsedData.imageId, firstCell).then((reading) => {
      void followProfile({ firstRowAtTop: reading?.y === 1, searchRadiusPx: searchRadiusFor(shownSize.w), turn });
    });
    // The search radius reads the zoom once, when the new image is first drawn.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profileOpen, mainDrawn, currentKey, parsedData, followProfile]);

  // The marker of the measured star: a ring at the refined center and the line of the cut.
  const profileMarker = ((): ProfileMarker | null => {
    const profileState = starProfile.state;
    if (!parsedData || !mainDrawn || drawnSize.w <= 0 || drawnSize.h <= 0) return null;
    if (profileState.status !== 'ready' && profileState.status !== 'measuring') return null;
    const measurement = profileState.status === 'ready' ? profileState.measurement : profileState.previous;
    const firstRowAtTop = profileState.anchor.firstRowAtTop;
    const centerX = measurement ? measurement.center_x : profileState.anchor.x;
    const centerY = measurement ? measurement.center_y : profileState.anchor.y;
    // Screen pixels for one file pixel, along the screen's horizontal and vertical axes.
    const stepX = (drawnSize.w / shownSize.w) * effectiveZoom;
    const stepY = (drawnSize.h / shownSize.h) * effectiveZoom;
    /** Finds the screen position of a FITS pixel position, with the picture's row order and turn. */
    const screenPositionOf = (fitsX: number, fitsY: number) => {
      const place = fitsPixelToTurnedPicture(fitsX, fitsY, parsedData.w, parsedData.h, turn, firstRowAtTop);
      return { x: panX + place.x * stepX, y: panY + place.y * stepY };
    };
    const { x: screenX, y: screenY } = screenPositionOf(centerX, centerY);
    // The ring is one FWHM across (radius is half of it), in the size the picture is drawn at.
    const fittedWidth = measurement?.moffat.fitted ? measurement.moffat.fwhm_px : measurement?.gaussian.fitted ? measurement.gaussian.fwhm_px : null;
    const starFound = measurement ? measurement.star_found : true;
    // A profile that is not a star makes no width claim, so it gets a small marker of fixed size.
    const radius = starFound ? ((fittedWidth ?? 3) / 2) * stepX : NOT_A_STAR_MARKER_RADIUS_PX;
    let cut: { x1: number; y1: number; x2: number; y2: number } | null = null;
    let band: { inner: number; outer: number } | null = null;
    let neighbors: Array<{ x: number; y: number; radius: number }> = [];
    if (measurement && profileView?.mode === 'ring') {
      band = { inner: profileView.ringInnerPx * stepX, outer: profileView.ringOuterPx * stepX };
      // The neighbor stars the library left out of the ring, as circles of their mask radius.
      neighbors = measurement.ring_neighbors.map((neighbor) => ({
        ...screenPositionOf(neighbor.x, neighbor.y),
        radius: neighbor.mask_radius_px * stepX,
      }));
    } else if (measurement) {
      // The angle runs from +y (up a column) toward +x; screen rows grow downward when the first row is at the top.
      // A turned picture turns the cut with it.
      const angle = (starProfile.cutAngleDeg * Math.PI) / 180;
      const halfLength = profileView?.cutHalfLengthPx ?? measurement.cut_half_length_px;
      const turnedHalfCut = turnStep(Math.sin(angle) * halfLength, Math.cos(angle) * halfLength * (firstRowAtTop ? 1 : -1), turn);
      const alongX = turnedHalfCut.x * stepX;
      const alongRows = turnedHalfCut.y * stepY;
      cut = { x1: screenX - alongX, y1: screenY - alongRows, x2: screenX + alongX, y2: screenY + alongRows };
    }
    return { screenX, screenY, radius, cut, band, neighbors, starFound };
  })();

  // The name of the measured star for the top of the profile panel: the overlay star at its center, or
  // "Unnamed star" with its sky position when the image is plate-solved.
  const profileStarName = useMemo(() => {
    const profileState = starProfile.state;
    if (profileState.status !== 'ready') return null;
    return nameStarAt(
      profileState.measurement.center_x,
      profileState.measurement.center_y,
      showOverlay ? overlayStarsAsSent : undefined,
      parsedData?.header ? createPixelToSky(parsedData.header) : null,
    );
  }, [starProfile.state, showOverlay, overlayStarsAsSent, parsedData]);

  // The overlay star at the measured center, when the catalog names it: its record fills the identity line.
  const profileCatalogStar = useMemo(() => {
    const profileState = starProfile.state;
    if (profileState.status !== 'ready' || !showOverlay) return null;
    const star = findStarAt(profileState.measurement.center_x, profileState.measurement.center_y, overlayStarsAsSent);
    return star && canOpenInAstronomyManager(star) ? star : null;
  }, [starProfile.state, showOverlay, overlayStarsAsSent]);
  const profileStarIdentity = useStarIdentity(profileCatalogStar?.libraryId ?? null);
  const openProfileStarInManager = useMemo(
    () => (profileCatalogStar && onStarClick ? () => onStarClick(profileCatalogStar) : undefined),
    [profileCatalogStar, onStarClick],
  );

  const profilePanelOpen = starProfile.state.status === 'ready';

  return {
    starProfile, setProfileView, measureStarAt, profileMarker, profileStarName, profileStarIdentity,
    openProfileStarInManager, profilePanelOpen,
  };
}
