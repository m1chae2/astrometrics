/**
 * @file useBitmapTurn.ts
 * @description Works out how far to turn a picture the backend already drew (an Auto Stretch picture
 * or the quick preview), so it is shown the same way round as the target's stack.
 *
 * A raw frame in Linear mode holds its own header, so the viewer decides its turn at once. A drawn
 * picture has no header, so the header of the file behind it is read from the backend, and the same
 * rule (`turnToMatchStack`) gives the same turn. The picture is held back until the turn is known, so
 * it is never drawn the wrong way round and then flipped.
 */
import { useEffect, useState } from 'react';
import { fetchTargetFrameHeader } from '../../services/targetService';
import { headerEntriesToCards } from '../plateGeometry';
import { turnToMatchStack } from '../displayTurn';
import type { QuarterTurn, StackOrientation } from '../displayTurn';
import type { FitsHeaderCards } from '../wcs';

/** The answer of {@link useBitmapTurn}. */
export interface BitmapTurn {
  /** The turn to draw the picture with; 0 until `ready`. */
  turn: QuarterTurn;
  /** True when the turn is known. A picture should be drawn only when this is true. */
  ready: boolean;
}

/** How many headers are kept, so switching between two modes does not ask again. */
const HEADER_CACHE_LIMIT = 32;

/**
 * Decides the turn of a picture that has no header of its own.
 *
 * Nothing is asked when there is no stack orientation, no target or no way to find the file: the
 * picture is then shown as it is. A header that cannot be read is not an error either.
 *
 * @param imageKey What the picture was made from (its blob or address); a new key starts a new lookup.
 * @param stackOrientation The stack's orientation, or null when the target has none.
 * @param targetId The selected target, or null.
 * @param resolveMeasurePath Finds the file path behind the picture on screen, or null when unknown.
 * @param enabled False while a decoded FITS image is shown, which knows its own header.
 * @returns The turn and whether it is known yet.
 */
export function useBitmapTurn(
  imageKey: string | Blob | null,
  stackOrientation: StackOrientation | null,
  targetId: string | null,
  resolveMeasurePath: (() => Promise<string | null>) | undefined,
  enabled: boolean,
): BitmapTurn {
  const [found, setFound] = useState<{ key: string | Blob; stack: StackOrientation; turn: QuarterTurn } | null>(null);
  const [headers] = useState(() => new Map<string, FitsHeaderCards>());
  const needsLookup = enabled && imageKey !== null && stackOrientation !== null && targetId !== null && resolveMeasurePath !== undefined;

  useEffect(() => {
    if (!needsLookup || imageKey === null || stackOrientation === null || !targetId || !resolveMeasurePath) return undefined;
    let cancelled = false;
    /** Records the turn for this picture. */
    const settle = (cards: FitsHeaderCards | null) => {
      if (!cancelled) setFound({ key: imageKey, stack: stackOrientation, turn: turnToMatchStack(cards, stackOrientation) });
    };
    resolveMeasurePath()
      .then(async (path) => {
        if (!path) return settle(null);
        const cached = headers.get(path);
        if (cached) return settle(cached);
        const entries = await fetchTargetFrameHeader(targetId, path, { silent: true });
        const cards = headerEntriesToCards(entries);
        if (headers.size >= HEADER_CACHE_LIMIT) headers.delete(headers.keys().next().value as string);
        headers.set(path, cards);
        return settle(cards);
      })
      .catch(() => settle(null));
    return () => { cancelled = true; };
  }, [needsLookup, imageKey, stackOrientation, targetId, resolveMeasurePath, headers]);

  if (!needsLookup) return { turn: 0, ready: true };
  if (found && found.key === imageKey && found.stack === stackOrientation) return { turn: found.turn, ready: true };
  // While the lookup runs, the picture on screen keeps the turn it was drawn with.
  return { turn: found?.turn ?? 0, ready: false };
}
