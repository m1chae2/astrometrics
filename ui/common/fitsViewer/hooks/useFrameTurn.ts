/**
 * @file useFrameTurn.ts
 * @description Asks the backend how far to turn the picture on screen so it is shown the same way round
 * as the target's stack. The backend (the library) decides the quarter turn from the headers of the file
 * behind the picture and of the stack, for a decoded FITS image and for a picture the backend drew (an
 * Auto Stretch picture or the quick preview) alike. The picture is held back until the turn is known, so
 * it is never drawn the wrong way round and then flipped.
 */
import { useEffect, useState } from 'react';
import { fetchFrameDisplayTurn } from '../../services/targetService';
import { asQuarterTurn } from '../displayTurn';
import type { QuarterTurn } from '../displayTurn';

/** The answer of {@link useFrameTurn}. */
export interface FrameTurn {
  /** The turn to draw the picture with; the last known turn until `ready`. */
  turn: QuarterTurn;
  /** True when the turn is known. A picture should be drawn only when this is true. */
  ready: boolean;
}

/** How many turns are kept, so switching between two modes does not ask again. */
const TURN_CACHE_LIMIT = 32;

/**
 * Decides the turn of the picture on screen.
 *
 * Nothing is asked when turning is off, or there is no target or no way to find the file: the picture is
 * then shown as it is. An answer that cannot be read is not an error either; the picture is shown as it is.
 *
 * @param imageKey What the picture was made from (its blob or address); a new key starts a new lookup.
 * @param targetId The selected target, or null.
 * @param resolveMeasurePath Finds the file path behind the picture on screen, or null when unknown.
 * @param enabled False when frames are shown as they are stored.
 * @returns The turn and whether it is known yet.
 */
export function useFrameTurn(
  imageKey: string | Blob | null,
  targetId: string | null,
  resolveMeasurePath: (() => Promise<string | null>) | undefined,
  enabled: boolean,
): FrameTurn {
  const [found, setFound] = useState<{ key: string | Blob; target: string; turn: QuarterTurn } | null>(null);
  const [turns] = useState(() => new Map<string, QuarterTurn>());
  const needsLookup = enabled && imageKey !== null && targetId !== null && resolveMeasurePath !== undefined;

  useEffect(() => {
    if (!needsLookup || imageKey === null || !targetId || !resolveMeasurePath) return undefined;
    let cancelled = false;
    /** Records the turn for this picture. */
    const settle = (turn: QuarterTurn) => {
      if (!cancelled) setFound({ key: imageKey, target: targetId, turn });
    };
    resolveMeasurePath()
      .then(async (path) => {
        if (!path) return settle(0);
        const known = turns.get(`${targetId}\n${path}`);
        if (known !== undefined) return settle(known);
        const turn = asQuarterTurn(await fetchFrameDisplayTurn(targetId, path));
        if (turns.size >= TURN_CACHE_LIMIT) turns.delete(turns.keys().next().value as string);
        turns.set(`${targetId}\n${path}`, turn);
        return settle(turn);
      })
      .catch(() => settle(0));
    return () => { cancelled = true; };
  }, [needsLookup, imageKey, targetId, resolveMeasurePath, turns]);

  if (!needsLookup) return { turn: 0, ready: true };
  if (found && found.key === imageKey && found.target === targetId) return { turn: found.turn, ready: true };
  // While the lookup runs, the picture on screen keeps the turn it was drawn with.
  return { turn: found?.turn ?? 0, ready: false };
}
