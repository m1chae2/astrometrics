// Small helper for focal-point pan deltas when zooming.
// Exports a pure function so it can be unit tested.
export function computeFocalPanDelta(
  oldZoom: number,
  newZoom: number,
  pointerX: number,
  pointerY: number,
  dispPanX: number,
  dispPanY: number
): { dx: number; dy: number } {
  if (oldZoom === 0) return { dx: 0, dy: 0 };
  const factor = 1 - newZoom / oldZoom;
  const dx = factor * (pointerX - dispPanX);
  const dy = factor * (pointerY - dispPanY);
  return { dx, dy };
}

export default computeFocalPanDelta;
