/**
 * @file useStackOrientation.ts
 * @description Reads the target's stack header and reports which way round the stack is, so the FITS
 * viewer can show each raw frame turned the same way (see `displayTurn.ts`). The frames of a target
 * that crossed the meridian are stored upside down from each other; the stack is the reference.
 */
import { useEffect, useState } from 'react';
import { fetchTargetFrameHeader } from '../../common/services/targetService';
import { headerEntriesToCards, measurePlateGeometry } from '../../common/fitsViewer/plateGeometry';
import { stackPierSide } from '../../common/fitsViewer/displayTurn';
import type { StackOrientation } from '../../common/fitsViewer/displayTurn';

/**
 * Finds the stack's orientation from its header.
 *
 * Nothing is asked when there is no stack or the stack's path does not belong to the target. A stack
 * without a plate solution has no orientation, so frames are then shown as they are stored. A header
 * that cannot be read is not an error: the frames are simply shown as stored.
 *
 * @param targetId The selected target, or null.
 * @param stackPath The path of the target's stack file, or null when it has none.
 * @returns The orientation, or null while it is unknown.
 */
export function useStackOrientation(targetId: string | null, stackPath: string | null): StackOrientation | null {
  const [orientation, setOrientation] = useState<{ key: string; value: StackOrientation | null } | null>(null);
  const key = targetId && stackPath ? `${targetId}\n${stackPath}` : null;

  useEffect(() => {
    if (!targetId || !stackPath || !key) return undefined;
    // The stack's file name carries the target's name; a path from another target is not asked for.
    const normalizedTarget = targetId.toLowerCase().replace(/[^a-z0-9]/g, '');
    const normalizedPath = stackPath.toLowerCase().replace(/[^a-z0-9]/g, '');
    if (!normalizedPath.includes(normalizedTarget)) return undefined;
    let cancelled = false;
    fetchTargetFrameHeader(targetId, stackPath, { silent: true })
      .then((entries) => {
        if (cancelled) return;
        const cards = headerEntriesToCards(entries);
        const geometry = measurePlateGeometry(cards);
        setOrientation({
          key,
          value: geometry ? { upAngleDegrees: geometry.upAngleDegrees, pierSide: stackPierSide(cards) } : null,
        });
      })
      .catch(() => {
        if (!cancelled) setOrientation({ key, value: null });
      });
    return () => { cancelled = true; };
  }, [targetId, stackPath, key]);

  return orientation !== null && orientation.key === key ? orientation.value : null;
}
