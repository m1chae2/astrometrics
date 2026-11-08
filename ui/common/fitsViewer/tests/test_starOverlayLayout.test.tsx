/**
 * @file test_starOverlayLayout.test.tsx
 * @description Tests for the star overlay: every star gets a circle, labels
 * follow a fixed budget on the stars in view, and hovering any circle shows
 * the star's name, and clicking any circle (but not ending a pan on one) opens the star.
 */
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, fireEvent, createEvent } from '@testing-library/react';
import {
  STAR_LABEL_BUDGET,
  StarScreenTransform,
  chooseLabeledStarIds,
  findStarNearPointer
} from '../utils/starOverlayLayout';
import { estimateBadgeWidth } from '../FitsRenderer';

// A fixed decoded picture, so the renderer treats the full image as ready.
const FAKE_BITMAP = { width: 800, height: 1000 } as unknown as ImageBitmap;
vi.mock('../hooks/useFitsLoader', () => ({
  useFitsLoader: () => ({
    parsedData: null, bitmap: FAKE_BITMAP, workerRef: { current: null },
    phase: 'ready', errorMessage: null, loadedKey: 'fake.fits'
  })
}));
vi.mock('../hooks/useCanvasDrawer', () => ({
  /**
   * Stand-in drawer that reports an 800 by 1000 drawn image once.
   */
  useCanvasDrawer: (
    _canvas: unknown, _worker: unknown, _data: unknown, bitmap: ImageBitmap | null,
    setDrawnSize: (size: { w: number; h: number }) => void,
    _stretch?: boolean, _range?: unknown, _parameters?: unknown,
    onDrawn?: (drawn: ImageBitmap) => void
  ) => {
    React.useEffect(() => {
      setDrawnSize({ w: 800, h: 1000 });
      if (bitmap) onDrawn?.(bitmap);
    }, [setDrawnSize, bitmap, onDrawn]);
  }
}));

/**
 * Builds a grid of stars, 40 columns by 50 rows, 100 image pixels apart.
 *
 * @returns Two thousand overlay stars, brightest (first) at the top left.
 */
function makeGridStars() {
  const stars = [];
  for (let row = 0; row < 50; row++) {
    for (let column = 0; column < 40; column++) {
      stars.push({
        id: `S${row}_${column}`, name: `Star ${row}-${column}`, x: column * 100 + 50, y: row * 100 + 50,
        spectralType: 'G2V', isCatalogIdentified: true, referenceWidth: 4000, referenceHeight: 5000
      });
    }
  }
  return stars;
}

const fitTransform: StarScreenTransform = {
  scaleX: 0.2, scaleY: 0.2, panX: 0, panY: 0, viewWidth: 800, viewHeight: 1000
};

describe('chooseLabeledStarIds', () => {
  /**
   * At fit, only the label budget is spent even though 2,000 stars are in view.
   */
  it('labels no more than the budget at fit', () => {
    const labeled = chooseLabeledStarIds(makeGridStars(), fitTransform, estimateBadgeWidth);
    expect(labeled.size).toBeLessThanOrEqual(STAR_LABEL_BUDGET);
    expect(labeled.size).toBeGreaterThan(0);
  });

  /**
   * Zoomed onto the bottom right corner, labels come only from stars in view.
   */
  it('chooses labels from the stars inside the zoomed area', () => {
    const stars = makeGridStars();
    const zoomed: StarScreenTransform = {
      scaleX: 2, scaleY: 2, panX: -6000, panY: -8000, viewWidth: 800, viewHeight: 1000
    };
    const labeled = chooseLabeledStarIds(stars, zoomed, estimateBadgeWidth);
    expect(labeled.size).toBeGreaterThan(0);
    expect(labeled.size).toBeLessThanOrEqual(STAR_LABEL_BUDGET);
    for (const star of stars.filter((item) => labeled.has(item.id))) {
      const screenX = -6000 + star.x * 2;
      const screenY = -8000 + star.y * 2;
      expect(screenX >= 0 && screenX <= 800 && screenY >= 0 && screenY <= 1000).toBe(true);
    }
  });

  /**
   * Zooming in shows more labels per image area than fit does.
   */
  it('shows more labels per area when zoomed in', () => {
    const stars = makeGridStars();
    const zoomed: StarScreenTransform = {
      scaleX: 2, scaleY: 2, panX: 0, panY: 0, viewWidth: 400, viewHeight: 400
    };
    const inZoomedArea = stars.filter((star) => star.x * 2 <= 400 && star.y * 2 <= 400);
    const labeled = chooseLabeledStarIds(stars, zoomed, estimateBadgeWidth);
    // 4 x 4 stars are in view at 2x, and all can be labeled; at fit that area holds almost none.
    const fitLabeled = chooseLabeledStarIds(stars, fitTransform, estimateBadgeWidth);
    const fitInArea = inZoomedArea.filter((star) => fitLabeled.has(star.id)).length;
    expect(labeled.size).toBeGreaterThan(fitInArea);
  });
});

describe('findStarNearPointer', () => {
  /**
   * A pointer on a star finds it; a pointer far from every star finds none.
   */
  it('finds the star under the pointer only when close enough', () => {
    const stars = makeGridStars();
    expect(findStarNearPointer(stars, fitTransform, 10, 10)?.id).toBe('S0_0');
    expect(findStarNearPointer(stars, fitTransform, 20, 20)).toBeNull();
  });
});

describe('FitsRenderer star overlay', () => {
  /**
   * Renders the viewer with the overlay on a sized container.
   *
   * @param stars The overlay stars.
   * @returns The testing-library render result.
   */
  async function renderOverlay(stars: ReturnType<typeof makeGridStars>, onStarClick?: (star: unknown) => void) {
    const { FitsRenderer } = await import('../FitsRenderer');
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(800);
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(1000);
    // jsdom has no PointerEvent; a mouse event carries the same coordinates.
    vi.stubGlobal('PointerEvent', MouseEvent);
    // jsdom also lacks pointer capture, which the pan code uses.
    Element.prototype.setPointerCapture = vi.fn();
    Element.prototype.releasePointerCapture = vi.fn();
    const strokeCounts = { arcs: 0 };
    HTMLCanvasElement.prototype.getContext = vi.fn(() => ({
      setTransform: vi.fn(), clearRect: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), stroke: vi.fn(),
      arc: vi.fn(() => { strokeCounts.arcs += 1; })
    })) as unknown as typeof HTMLCanvasElement.prototype.getContext;
    const result = render(
      <FitsRenderer
        imageUrl="fake.fits" imageBlob={null} frameInfo={null} loading={false} error={null}
        selectedTarget="M 31" autoPanTrigger={0} overlayStars={stars} showOverlay onStarClick={onStarClick}
      />
    );
    return { ...result, strokeCounts };
  }

  /**
   * Every star gets a circle, but only the budget gets a label badge.
   */
  it('draws a circle for every star and labels only the budget', async () => {
    const stars = makeGridStars();
    const { container, strokeCounts } = await renderOverlay(stars);
    expect(container.querySelectorAll('.fits-renderer__star-group').length).toBeLessThanOrEqual(STAR_LABEL_BUDGET);
    expect(container.querySelectorAll('.fits-renderer__star-group').length).toBeGreaterThan(0);
    expect(strokeCounts.arcs).toBeGreaterThan(0);
  });

  /**
   * Hovering an unlabeled star shows its tooltip with the name only.
   */
  it('shows a tooltip when hovering a star that has no label', async () => {
    const stars = makeGridStars();
    const { container } = await renderOverlay(stars);
    const viewer = container.querySelector('.fits-renderer__canvas-container') as HTMLElement;
    // Star S49_39 sits at the bottom right of the fitted image (zoom 0.95, pan 20 and 25), away from the labels.
    expect(container.querySelector('.fits-renderer__star-tooltip')).toBeNull();
    fireEvent.pointerMove(viewer, { clientX: 770.5, clientY: 965.5 });
    const tooltip = container.querySelector('.fits-renderer__star-tooltip');
    expect(tooltip?.textContent).toContain('Star 49-39');
    expect(tooltip?.textContent).toBe('Open Star 49-39 in the Astronomy Manager');
    expect(tooltip?.textContent).not.toContain('G2V');
  });

  /**
   * Label badges show the star name and no spectral type.
   */
  it('labels show the name without the spectral type', async () => {
    const { container } = await renderOverlay(makeGridStars());
    const badges = container.querySelector('.fits-renderer__overlay')?.textContent ?? '';
    expect(badges).toContain('Star 0-0');
    expect(badges).not.toContain('G2V');
    expect(container.querySelector('.fits-renderer__badge-type')).toBeNull();
  });

  /**
   * A press and release in place on an unlabeled circle opens that star.
   */
  it('opens a star when an unlabeled circle is clicked', async () => {
    const onStarClick = vi.fn();
    const { container } = await renderOverlay(makeGridStars(), onStarClick);
    const viewer = container.querySelector('.fits-renderer__canvas-container') as HTMLElement;
    fireEvent.pointerDown(viewer, { clientX: 770.5, clientY: 965.5 });
    fireEvent.pointerUp(viewer, { clientX: 771, clientY: 966 });
    expect(onStarClick).toHaveBeenCalledTimes(1);
    expect(onStarClick.mock.calls[0][0]).toMatchObject({ id: 'S49_39' });
  });

  /**
   * A label badge opens its star through the same callback.
   */
  it('opens a star when a label badge is clicked', async () => {
    const onStarClick = vi.fn();
    const { container } = await renderOverlay(makeGridStars(), onStarClick);
    const badge = container.querySelector('.fits-renderer__star-group') as HTMLElement;
    fireEvent.pointerDown(badge, { clientX: 100, clientY: 100 });
    fireEvent.pointerUp(badge, { clientX: 100, clientY: 100 });
    expect(onStarClick).toHaveBeenCalledTimes(1);
    expect(onStarClick.mock.calls[0][0]).toMatchObject({ id: badge.getAttribute('data-star-id') });
  });

  /**
   * Enter and Space on a focused star open it, and Space does not scroll the page.
   */
  it('opens a star with Enter or Space', async () => {
    const onStarClick = vi.fn();
    const { container } = await renderOverlay(makeGridStars(), onStarClick);
    const badge = container.querySelector('.fits-renderer__star-group') as HTMLElement;
    fireEvent.keyDown(badge, { key: 'Enter' });
    const space = createEvent.keyDown(badge, { key: ' ' });
    fireEvent(badge, space);
    expect(onStarClick).toHaveBeenCalledTimes(2);
    expect(space.defaultPrevented).toBe(true);
  });

  /**
   * A pan drag that ends on a circle does not open the star.
   */
  it('ignores a pan drag that ends over a circle', async () => {
    const onStarClick = vi.fn();
    const { container } = await renderOverlay(makeGridStars(), onStarClick);
    const viewer = container.querySelector('.fits-renderer__canvas-container') as HTMLElement;
    fireEvent.pointerDown(viewer, { clientX: 700, clientY: 900 });
    fireEvent.pointerUp(viewer, { clientX: 770.5, clientY: 965.5 });
    expect(onStarClick).not.toHaveBeenCalled();
  });
});
