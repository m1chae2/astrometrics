/**
 * @file useStarProfile.ts
 * @description Keeps the state of the star profile panel: what was last clicked, the answer from the
 * backend, and the angle of the cut. Each click calls `images:measure_star_profile`; a click that finds no star is ignored; an answer that
 * comes back after a newer question was sent is ignored. A new angle only changes the state: the cut is
 * rebuilt in the browser from the cutout in the answer, so the slider needs no call to the backend.
 *
 * A click that finds a star pins it: its image path and its 1-based position. When the viewer shows another image,
 * `follow` asks `images:map_position` where the pinned star is on the new image and measures it there. The
 * pin stays on the image the user clicked, so switching back finds the star again, and a failed follow
 * leaves it as it was.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { callBackend } from '../../services/backendApi';
import type { StarProfileMeasurement } from '../../types/backendTypes';

/** Milliseconds the cut length slider must rest before a longer cut is measured. */
export const CUT_LENGTH_DEBOUNCE_MS = 300;

/** The star the user clicked: the image it was measured on and its position there. */
export interface PinnedStar {
  /** The file the star was measured on. */
  path: string;
  /** FITS column, counted from 1. */
  x: number;
  /** FITS row, counted from 1. */
  y: number;
}

/** How a followed star was found on the new image. */
export interface FollowNote {
  /** "sky", "stack alignment", "stack alignment + sky" or "same pixel". */
  method: string;
  /** The backend's sentence about it. */
  note: string;
}

/** Smallest search radius, in image pixels, when the star's place on a new image is only a guess. */
export const GUESS_SEARCH_RADIUS_PX = 10;

/** The pixel the user clicked, and how the picture was laid out when they did. */
export interface ProfileAnchor {
  /** FITS column, counted from 1. */
  x: number;
  /** FITS row, counted from 1. */
  y: number;
  /** How far from the click the backend may look for the star's center, in image pixels. */
  searchRadiusPx: number;
  /** True when the picture shows the file's first row at the top; the marker needs it to turn rows into screen positions. */
  firstRowAtTop: boolean;
}

/** What the panel shows. */
export type StarProfileState =
  | { status: 'closed' }
  | { status: 'unavailable'; message: string }
  | { status: 'measuring'; anchor: ProfileAnchor; previous: StarProfileMeasurement | null }
  | { status: 'error'; anchor: ProfileAnchor; message: string }
  | { status: 'ready'; anchor: ProfileAnchor; measurement: StarProfileMeasurement };

/** What {@link useStarProfile} hands back. */
export interface StarProfileController {
  /** What the panel shows now. */
  state: StarProfileState;
  /** The angle of the cut, in degrees, 0 to 179. */
  cutAngleDegrees: number;
  /** Measures the star at a clicked pixel. Opens the panel only when a star is found there. */
  measureAt: (anchor: ProfileAnchor) => void;
  /** Shows a message in place of a measurement, without calling the backend. */
  showUnavailable: (message: string) => void;
  /** Sets the angle of the cut. Nothing is sent to the backend. */
  setCutAngle: (degrees: number) => void;
  /** The cut half-length the user chose, in pixels, or null while the measurement's own length is used. */
  cutLengthPx: number | null;
  /**
   * Sets the cut half-length. A length the cutout already covers only changes the cut on screen; a longer one
   * measures the star again, once, after the slider has rested.
   */
  setCutLength: (lengthPx: number) => void;
  /** The pinned star, or null when none was clicked. */
  pin: PinnedStar | null;
  /** How the star on screen was found after the image changed, or null for a star the user clicked. */
  followNote: FollowNote | null;
  /**
   * Finds the pinned star on the image now shown and measures it there. The pin is left as it was, and a failure
   * is shown in the panel.
   */
  follow: (options: { firstRowAtTop: boolean; searchRadiusPx: number }) => Promise<void>;
  /** Closes the panel and forgets the star. */
  close: () => void;
}

/**
 * Turns a failure into a sentence for the panel.
 *
 * @param failure Whatever was thrown.
 * @returns The sentence.
 */
function describeFailure(failure: unknown): string {
  return failure instanceof Error && failure.message ? failure.message : 'The star could not be measured.';
}

/**
 * Runs the star profile measurements for one image.
 *
 * @param resolvePath Finds the file path of the image on screen; called for every measurement, so it can follow a changing image.
 * @returns The state and the actions that change it.
 */
export function useStarProfile(resolvePath: (() => Promise<string | null>) | undefined): StarProfileController {
  const [state, setState] = useState<StarProfileState>({ status: 'closed' });
  const [cutAngleDegrees, setCutAngleDegrees] = useState(0);
  const [cutLengthPx, setCutLengthPx] = useState<number | null>(null);
  const cutLengthPxRef = useRef<number | null>(null);
  const lengthTimerRef = useRef<number | null>(null);
  const [pin, setPin] = useState<PinnedStar | null>(null);
  const pinRef = useRef<PinnedStar | null>(null);
  const [followNote, setFollowNote] = useState<FollowNote | null>(null);
  const latestRequestRef = useRef(0);
  const anchorRef = useRef<ProfileAnchor | null>(null);
  // The newest angle, for a click that comes after the slider moved.
  const cutAngleDegreesRef = useRef(cutAngleDegrees);
  // The last good answer, kept on screen while the next one is on its way.
  const lastMeasurementRef = useRef<StarProfileMeasurement | null>(null);
  const resolvePathRef = useRef(resolvePath);
  useEffect(() => {
    resolvePathRef.current = resolvePath;
  });

  /**
   * Calls the backend for one star and files the answer, unless a newer question was sent meanwhile.
   *
   * A click of the user ("user click") that finds no star is ignored whole: nothing opens, no marker is
   * drawn, and a star already on screen and the pinned star stay as they were. The panel and the marker wait
   * for the answer, so there is no flash. A followed star that is not found is shown, so the user sees the follow failed.
   *
   * @param anchor The pixel to measure.
   * @param angle The angle of the cut.
   * @param halfLengthPx The cut half-length to ask for, or null for the backend's default.
   * @param userClick True for a click of the user, whose star becomes the pinned one.
   */
  const run = useCallback(async (anchor: ProfileAnchor, angle: number, halfLengthPx: number | null, userClick = false) => {
    const requestId = ++latestRequestRef.current;
    // A click waits for its answer; the old star, if any, stays on screen and in `anchorRef` until then.
    if (!userClick) {
      anchorRef.current = anchor;
      setState({ status: 'measuring', anchor, previous: lastMeasurementRef.current });
    }
    try {
      const path = await resolvePathRef.current?.();
      if (requestId !== latestRequestRef.current) return;
      if (!path) {
        anchorRef.current = anchor;
        setState({ status: 'error', anchor, message: 'This image has no file the backend can measure.' });
        return;
      }
      const measurement = await callBackend(
        'images:measure_star_profile',
        {
          path, x: anchor.x, y: anchor.y, cut_angle_degrees: angle, search_radius_px: anchor.searchRadiusPx,
          ...(halfLengthPx !== null ? { cut_half_length_px: halfLengthPx } : {}),
        },
        { silent: true },
      );
      if (requestId !== latestRequestRef.current) return;
      if (userClick) {
        // Empty sky: the click did nothing.
        if (!measurement.star_found) return;
        anchorRef.current = anchor;
        setFollowNote(null);
        const pinned = { path, x: measurement.center_x, y: measurement.center_y };
        pinRef.current = pinned;
        setPin(pinned);
      }
      lastMeasurementRef.current = measurement;
      setState({ status: 'ready', anchor, measurement });
    } catch (failure) {
      if (requestId !== latestRequestRef.current) return;
      anchorRef.current = anchor;
      setState({ status: 'error', anchor, message: describeFailure(failure) });
    }
  }, []);

  const measureAt = useCallback((anchor: ProfileAnchor) => {
    void run(anchor, cutAngleDegreesRef.current, cutLengthPxRef.current, true);
  }, [run]);

  const follow = useCallback(async ({ firstRowAtTop, searchRadiusPx }: { firstRowAtTop: boolean; searchRadiusPx: number }) => {
    const pinned = pinRef.current;
    if (!pinned) return;
    const requestId = ++latestRequestRef.current;
    lastMeasurementRef.current = null;
    const waiting: ProfileAnchor = { x: pinned.x, y: pinned.y, searchRadiusPx, firstRowAtTop };
    setState({ status: 'measuring', anchor: waiting, previous: null });
    try {
      const path = await resolvePathRef.current?.();
      if (requestId !== latestRequestRef.current) return;
      if (!path) {
        setState({ status: 'unavailable', message: 'This image has no file the backend can measure.' });
        return;
      }
      if (path === pinned.path) {
        // The same file (for example, the display mode changed): the pinned position is already right.
        setFollowNote(null);
        void run(waiting, cutAngleDegreesRef.current, cutLengthPxRef.current);
        return;
      }
      const mapped = await callBackend(
        'images:map_position',
        { from_path: pinned.path, x: pinned.x, y: pinned.y, to_path: path },
        { silent: true },
      );
      if (requestId !== latestRequestRef.current) return;
      if (mapped.x === null || mapped.y === null) {
        setState({ status: 'unavailable', message: `The star could not be followed to this image: ${mapped.note}` });
        return;
      }
      setFollowNote({ method: mapped.method, note: mapped.note });
      // A guess needs a wider look, since the star may be some pixels away from the same pixel.
      const radius = mapped.method === 'same pixel' ? Math.max(searchRadiusPx, GUESS_SEARCH_RADIUS_PX) : searchRadiusPx;
      void run({ x: mapped.x, y: mapped.y, searchRadiusPx: radius, firstRowAtTop }, cutAngleDegreesRef.current, cutLengthPxRef.current);
    } catch (failure) {
      if (requestId !== latestRequestRef.current) return;
      setState({ status: 'unavailable', message: `The star could not be followed to this image: ${describeFailure(failure)}` });
    }
  }, [run]);

  const showUnavailable = useCallback((message: string) => {
    latestRequestRef.current++;
    anchorRef.current = null;
    lastMeasurementRef.current = null;
    setState({ status: 'unavailable', message });
  }, []);

  const setCutAngle = useCallback((degrees: number) => {
    setCutAngleDegrees(degrees);
    cutAngleDegreesRef.current = degrees;
  }, []);

  const setCutLength = useCallback((lengthPx: number) => {
    setCutLengthPx(lengthPx);
    cutLengthPxRef.current = lengthPx;
    if (lengthTimerRef.current !== null) window.clearTimeout(lengthTimerRef.current);
    lengthTimerRef.current = window.setTimeout(() => {
      lengthTimerRef.current = null;
      const covered = lastMeasurementRef.current?.cut_half_length_px;
      // The cutout reaches as far as the length it was measured with; only a longer cut needs a new measurement.
      if (anchorRef.current && covered !== undefined && lengthPx > covered) {
        void run(anchorRef.current, cutAngleDegreesRef.current, lengthPx);
      }
    }, CUT_LENGTH_DEBOUNCE_MS);
  }, [run]);

  const close = useCallback(() => {
    if (lengthTimerRef.current !== null) window.clearTimeout(lengthTimerRef.current);
    lengthTimerRef.current = null;
    latestRequestRef.current++;
    anchorRef.current = null;
    lastMeasurementRef.current = null;
    pinRef.current = null;
    setPin(null);
    setFollowNote(null);
    setState({ status: 'closed' });
  }, []);

  return { state, pin, followNote, follow, cutAngleDegrees, measureAt, showUnavailable, setCutAngle, cutLengthPx, setCutLength, close };
}
