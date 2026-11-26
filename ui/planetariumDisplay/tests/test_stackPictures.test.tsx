/**
 * @fileoverview Tests of the Planetarium's Images layer: the triangle mesh built from a
 * picture's sky grid, drawing only while the layer is on, the toolbar's Images switch and
 * opacity, and no backend request while the layer is off.
 */

import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, renderHook, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const callBackendMock = vi.fn();
vi.mock('../../common/services/backendApi', () => ({
  callBackend: (...args: unknown[]) => callBackendMock(...args),
  loadStaticMounts: async () => undefined,
  resolveImageSrc: (path: string) => `http://backend/static/${path}`,
}));
vi.mock('../../common/components/PlanningTimeIndicator', () => ({ PlanningTimeIndicator: () => null }));

import { buildMesh, isTriangleOffCanvas, projectGrid, StackPictureOverlay } from '../layers/StackPictureOverlay';
import { useStackPictures } from '../hooks/useStackPictures';
import { PlanetariumToolbar } from '../components/PlanetariumToolbar';
import type { StackSkyPicture } from '../../common/types/planetariumTypes';
import type { ProjectionContext } from '../layers/overlayTypes';

/** A 3 x 3 grid picture 200 x 100 pixels, one degree across, around RA 10, Dec 20. */
const picture: StackSkyPicture = {
  targetId: 'M 31',
  picturePath: '/stacks/M_31_preview.jpg',
  width: 200,
  height: 100,
  gridSize: 3,
  grid: [0, 1, 2].flatMap((row) => [0, 1, 2].map((column) => [10.5 - column * 0.5, 20.25 - row * 0.25] as [number, number])),
};

/** A projection that puts RA and Dec straight onto the screen, 100 pixels per degree. */
const flatProjection = (ra: number, dec: number) => ({ x: (11 - ra) * 100, y: (21 - dec) * 100, visible: true, alt: 45 });

/**
 * A fake 2D canvas that records drawImage calls.
 * @returns The fake and its drawImage spy.
 */
function fakeContext() {
  const drawImage = vi.fn();
  const context = {
    save: vi.fn(), restore: vi.fn(), beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), closePath: vi.fn(),
    clip: vi.fn(), transform: vi.fn(), drawImage, globalAlpha: 1, imageSmoothingEnabled: true,
    setTransform: vi.fn(), clearRect: vi.fn(), getTransform: vi.fn(() => ({})),
    canvas: { width: 400, height: 300 },
  };
  return { context: context as unknown as CanvasRenderingContext2D, drawImage, transform: context.transform };
}

describe('Images layer mesh', () => {
  /** Each grid cell gives two triangles whose picture corners are the cell's pixel corners. */
  it('builds two triangles per grid cell from the grid', () => {
    const triangles = buildMesh(picture, projectGrid(picture, flatProjection));
    expect(triangles).toHaveLength(2 * 2 * 2);
    expect(triangles[0].source).toEqual([[0, 0], [100, 0], [0, 50]]);
    // The first grid point (RA 10.5, Dec 20.25) lands where the projection puts it.
    expect(triangles[0].target[0]).toEqual([50, 75]);
  });

  /** A triangle with a corner off the visible side of the sky is left out. */
  it('leaves out triangles with a hidden corner', () => {
    // A point toward the back of the sky projects very far out and is marked not visible.
    const hideFirst = (ra: number, dec: number) => (ra === 10.5 && dec === 20.25
      ? { x: 1e7, y: 1e7, visible: false, alt: -60 }
      : flatProjection(ra, dec));
    expect(buildMesh(picture, projectGrid(picture, hideFirst, 400, 300))).toHaveLength(7);
  });
});

/** A projection that sends one grid point toward the back of the sky. */
const hideFar = (ra: number, dec: number) => (ra === 9.5 && dec === 19.75
  ? { x: -1e7, y: 1e7, visible: false, alt: -60 }
  : flatProjection(ra, dec));

describe('Images layer at a close zoom', () => {
  /** A triangle reaching past the view edge keeps its off-screen corners and is drawn. */
  it('draws a triangle that straddles the view edge', () => {
    // The projection marks points more than 100 px past the edge "not visible", as the map's does.
    const zoomed = (ra: number, dec: number) => {
      const x = (10.55 - ra) * 2000;
      const y = (20.3 - dec) * 2000;
      return { x, y, visible: x > -100 && x < 500 && y > -100 && y < 400, alt: 45 };
    };
    const screen = projectGrid(picture, zoomed, 400, 300);
    expect(projectGrid(picture, hideFar, 400, 300).some((point) => point === null)).toBe(true);
    expect(screen.every((point) => point !== null)).toBe(true);
    const triangles = buildMesh(picture, screen);
    expect(triangles).toHaveLength(8);
    const drawn = triangles.filter((triangle) => !isTriangleOffCanvas(triangle.target, 400, 300));
    // The first cell reaches past the right and bottom edges; it is drawn, not dropped.
    expect(drawn).toContain(triangles[0]);
    expect(triangles[0].target.some(([x]) => x > 500)).toBe(true);
  });

  /** Neighbouring triangles share the same corner points, so there is no gap between them. */
  it('makes neighbouring triangles share their edges', () => {
    const [first, second] = buildMesh(picture, projectGrid(picture, flatProjection));
    // The cell's diagonal: top-right and bottom-left corners are in both triangles.
    expect(second.target).toContainEqual(first.target[1]);
    expect(second.target).toContainEqual(first.target[2]);
    expect(second.source).toContainEqual(first.source[1]);
    expect(second.source).toContainEqual(first.source[2]);
  });
});

describe('Images layer drawing', () => {
  /**
   * A projection context for the overlay.
   * @param show Whether the layer is on.
   * @returns The context.
   */
  function projectionContext(show: boolean): ProjectionContext {
    const image = { naturalWidth: 200, naturalHeight: 100 } as HTMLImageElement;
    return {
      width: 400, height: 300, fov: 2, projectCoords: flatProjection,
      stackPictures: { show, opacity: 0.5, pictures: [picture], getDrawable: () => image },
    } as unknown as ProjectionContext;
  }

  /** Off: nothing is drawn. */
  it('draws nothing when the layer is off', () => {
    const { context, drawImage } = fakeContext();
    new StackPictureOverlay().draw(context, projectionContext(false));
    expect(drawImage).not.toHaveBeenCalled();
  });

  /** On: every triangle is drawn, at the layer's opacity. */
  it('draws every triangle when the layer is on', () => {
    const { context, drawImage } = fakeContext();
    const scratch = fakeContext();
    new StackPictureOverlay(() => scratch.context).draw(context, projectionContext(true));
    // Every triangle goes to the separate canvas at full strength...
    expect(scratch.drawImage).toHaveBeenCalledTimes(8);
    expect((scratch.context as unknown as { globalAlpha: number }).globalAlpha).toBe(1);
    // ...which is faded onto the sky once, so overlapping edges are not painted twice.
    expect(drawImage).toHaveBeenCalledTimes(1);
    expect((context as unknown as { globalAlpha: number }).globalAlpha).toBe(0.5);
  });

  /** A picture outside the view is not drawn or loaded. */
  it('skips a picture that is out of view', () => {
    const { context } = fakeContext();
    const scratch = fakeContext();
    const getDrawable = vi.fn();
    const offscreen = { ...projectionContext(true) };
    offscreen.projectCoords = (ra: number, dec: number) => ({ ...flatProjection(ra, dec), x: flatProjection(ra, dec).x + 5000 });
    offscreen.stackPictures = { ...offscreen.stackPictures!, getDrawable };
    new StackPictureOverlay(() => scratch.context).draw(context, offscreen);
    expect(scratch.drawImage).not.toHaveBeenCalled();
    expect(getDrawable).not.toHaveBeenCalled();
  });
});

describe('Images switch', () => {
  /** The toolbar has a compact Images switch beside Telescope and FOV Outline. */
  it('shows the Images switch and reports a change', async () => {
    const onToggleImages = vi.fn();
    const noop = (): void => undefined;
    render(
      <PlanetariumToolbar
        showStars onToggleStars={noop} showFOV onToggleFOV={noop} showEnvironment onToggleEnvironment={noop}
        showGrid onToggleGrid={noop} showCatalog onToggleCatalog={noop} showConstellations onToggleConstellations={noop}
        showTelescope onToggleTelescope={noop} currentFOV={10} onOpenTimeModal={noop}
        showImages={false} onToggleImages={onToggleImages} imagesOpacity={0.8} onImagesOpacityChange={noop}
      />,
    );
    const images = screen.getByRole('checkbox', { name: 'Images' });
    expect(images.closest('label')?.className).toContain('checkbox--compact');
    await userEvent.click(images);
    expect(onToggleImages).toHaveBeenCalledWith(true);
  });

  /** Off: the picture list is never asked for; on, it is, and the choice is remembered. */
  it('asks for the pictures only while on, and remembers the switch', async () => {
    localStorage.clear();
    callBackendMock.mockReset();
    callBackendMock.mockResolvedValue([picture]);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: React.ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
    const { result, rerender } = renderHook(() => useStackPictures(), { wrapper });
    expect(result.current.showImages).toBe(false);
    expect(callBackendMock).not.toHaveBeenCalled();
    result.current.setShowImages(true);
    rerender();
    await vi.waitFor(() => expect(result.current.pictures).toEqual([picture]));
    expect(callBackendMock).toHaveBeenCalledWith('planetarium:get_stack_pictures', {}, { silent: true });
    expect(localStorage.getItem('astrometrics:planetarium:showImages')).toBe('true');
  });
});

describe('Images layer order', () => {
  /** The pictures are drawn over the coordinate grid and under the constellation lines and markers. */
  it('draws the pictures above the grid and below the lines', async () => {
    const { readFileSync } = await import('node:fs');
    const { resolve } = await import('node:path');
    const source = readFileSync(resolve(__dirname, '../components/CelestialSkyMap.tsx'), 'utf8');
    const grid = source.indexOf('new GridOverlay()');
    const pictures = source.indexOf('new StackPictureOverlay()');
    const lines = source.indexOf('new ConstellationOverlay()');
    const targets = source.indexOf('new TargetOverlay()');
    expect(grid).toBeGreaterThan(-1);
    expect(pictures).toBeGreaterThan(grid);
    expect(lines).toBeGreaterThan(pictures);
    expect(targets).toBeGreaterThan(pictures);
  });
});

describe('Images layer coverage on M 31 (regression)', () => {
  /**
   * Draws M 31's real sky grid (from its stack's plate solution) through the map's real
   * projection, records the triangles the overlay draws, and checks that every sampled screen
   * point inside the picture is covered. The earlier version dropped every triangle with a
   * corner just off screen, which left 8 to 46 percent of the picture unpainted (black cells
   * on the dark sky) at 0.5 and 0.2 degree views.
   */
  it('covers the whole picture at close zooms, at the core and near the edges', async () => {
    const { default: m31 } = await import('./fixtures/m31StackGrid.json');
    const { getAltAz, projectAltAz } = await import('../utils/projectionMath');
    const width = 1600;
    const height = 1000;
    const views: [number, number, number][] = [[2, 10.68, 41.27], [0.5, 10.68, 41.27], [0.2, 10.68, 41.27], [0.5, 11.3, 41.9], [0.2, 10.0, 40.6]];
    for (const [fov, centerRa, centerDec] of views) {
      const latitude = 45.68;
      const lst = centerRa;
      const center = getAltAz(centerRa, centerDec, lst, latitude);
      const projectCoords = (ra: number, dec: number) => {
        const { alt, az } = getAltAz(ra, dec, lst, latitude);
        return { ...projectAltAz(alt, az, center.alt, center.az, fov, width, height), alt };
      };
      // Record each clip triangle the overlay draws.
      const triangles: [number, number][][] = [];
      let current: [number, number][] = [];
      const context = {
        save: () => undefined, restore: () => undefined, clip: () => triangles.push(current), transform: () => undefined,
        drawImage: () => undefined, closePath: () => undefined,
        beginPath: () => { current = []; },
        moveTo: (x: number, y: number) => { current.push([x, y]); },
        lineTo: (x: number, y: number) => { current.push([x, y]); },
        globalAlpha: 1, imageSmoothingEnabled: true,
        setTransform: () => undefined, clearRect: () => undefined, getTransform: () => ({}),
        canvas: { width, height },
      } as unknown as CanvasRenderingContext2D;
      const drawable = { naturalWidth: m31.width, naturalHeight: m31.height } as HTMLImageElement;
      new StackPictureOverlay(() => context).draw(context, {
        width, height, fov, projectCoords,
        stackPictures: { show: true, opacity: 1, pictures: [m31 as StackSkyPicture], getDrawable: () => drawable },
      } as unknown as ProjectionContext);

      const inTriangle = (x: number, y: number, [a, b, c]: [number, number][]) => {
        const side = (p: [number, number], q: [number, number]) => (q[0] - p[0]) * (y - p[1]) - (q[1] - p[1]) * (x - p[0]);
        const s1 = side(a, b); const s2 = side(b, c); const s3 = side(c, a);
        return (s1 >= 0 && s2 >= 0 && s3 >= 0) || (s1 <= 0 && s2 <= 0 && s3 <= 0);
      };
      // The picture's outline on screen, from its grid edge.
      const n = m31.gridSize;
      const edge = [
        ...Array.from({ length: n }, (_, j) => m31.grid[j]),
        ...Array.from({ length: n - 1 }, (_, i) => m31.grid[(i + 1) * n + n - 1]),
        ...Array.from({ length: n - 1 }, (_, j) => m31.grid[(n - 1) * n + n - 2 - j]),
        ...Array.from({ length: n - 2 }, (_, i) => m31.grid[(n - 2 - i) * n]),
      ].map(([ra, dec]) => { const p = projectCoords(ra, dec); return [p.x, p.y] as [number, number]; });
      const insideOutline = (x: number, y: number) => {
        let inside = false;
        for (let i = 0, j = edge.length - 1; i < edge.length; j = i++) {
          const [xi, yi] = edge[i]; const [xj, yj] = edge[j];
          if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
        }
        return inside;
      };
      let sampled = 0;
      let uncovered = 0;
      for (let y = 5; y < height; y += 20) {
        for (let x = 5; x < width; x += 20) {
          if (!insideOutline(x, y)) continue;
          sampled += 1;
          if (!triangles.some((triangle) => inTriangle(x, y, triangle))) uncovered += 1;
        }
      }
      expect(sampled, `view ${fov} deg at ${centerRa}, ${centerDec}`).toBeGreaterThan(100);
      expect(uncovered, `view ${fov} deg at ${centerRa}, ${centerDec}`).toBe(0);
    }
  });
});
