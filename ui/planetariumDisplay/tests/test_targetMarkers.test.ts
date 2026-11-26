/**
 * @fileoverview Tests of the Planetarium's library target markers: which marker a target
 * gets (none or a picture outline when its picture is drawn, a small ring or corner brackets
 * without one, an ellipse from a catalog shape), that the ellipse points the right way under
 * a turned sky, the picture outline built from the sky grid, the marker colors read from the
 * theme tokens, and that a click on a large marker still picks its target.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  chooseTargetMarker,
  footprintOutline,
  offsetOnSky,
  SMALL_MARKER_RADIUS_PX,
  TARGET_MARKER_MIN_PX,
  type TargetMarkerInput,
} from '../layers/targetMarkers';
import { TargetOverlay, shownPictureFor, picturesByTarget } from '../layers/TargetOverlay';
import { targetExtentDegrees } from '../layers/overlayShared';
import { clearThemeColors, MISSING_TOKEN_COLOR, readThemeColor } from '../utils/themeColor';
import { findNearestSource } from '../utils/hitTesting';
import type { PlanetariumTarget, StackSkyPicture } from '../../common/types/planetariumTypes';
import type { ProjectionContext } from '../layers/overlayTypes';

/** Pixels per degree of the stand-in projection. */
const SCALE = 100;

/**
 * A flat stand-in projection around RA 10, Dec 20: north up and east to the left, as on the
 * sky, turned on screen by `turnDeg` (clockwise).
 *
 * @param {number} turnDeg How far the sky is turned on screen, in degrees.
 * @returns {ProjectionContext['projectCoords']} The projection.
 */
function flatProjection(turnDeg = 0): ProjectionContext['projectCoords'] {
  const turn = (turnDeg * Math.PI) / 180;
  return (ra: number, dec: number) => {
    const east = (ra - 10) * Math.cos((20 * Math.PI) / 180);
    const north = dec - 20;
    // Unturned: east is -x, north is -y.
    const x0 = -east * SCALE;
    const y0 = -north * SCALE;
    return {
      x: 200 + x0 * Math.cos(turn) - y0 * Math.sin(turn),
      y: 150 + x0 * Math.sin(turn) + y0 * Math.cos(turn),
      visible: true,
      alt: 45,
    };
  };
}

/** A 3 x 3 grid picture one degree across around RA 10, Dec 20. */
const picture: StackSkyPicture = {
  targetId: 'M 81',
  picturePath: '/stacks/M_81_preview.jpg',
  width: 200,
  height: 100,
  gridSize: 3,
  grid: [0, 1, 2].flatMap((row) => [0, 1, 2].map((column) => [10.5 - column * 0.5, 20.25 - row * 0.25] as [number, number])),
};

/**
 * Builds the marker input for a target with the stand-in projection.
 *
 * @param {Partial<PlanetariumTarget>} target Target fields to set.
 * @param {Partial<TargetMarkerInput>} rest Other inputs to set.
 * @returns {TargetMarkerInput} The input.
 */
function input(target: Partial<PlanetariumTarget>, rest: Partial<TargetMarkerInput> = {}): TargetMarkerInput {
  return {
    target: { id: 'M 81', ra: 10, dec: 20, ...target },
    isSelected: false,
    shownPicture: null,
    projectCoords: flatProjection(),
    pixelsPerDeg: SCALE,
    width: 400,
    height: 300,
    ...rest,
  };
}

describe('target marker choice', () => {
  /** A drawn picture replaces the marker; only the selected target's picture gets an outline. */
  it('draws nothing for a target with a shown picture, and its outline when selected', () => {
    expect(chooseTargetMarker(input({}, { shownPicture: picture }))).toEqual({ kind: 'none' });
    const selected = chooseTargetMarker(input({}, { shownPicture: picture, isSelected: true }));
    expect(selected.kind).toBe('footprint');
  });

  /** Without a picture: a small ring of fixed size, or brackets the size of the object when selected. */
  it('draws a small ring, or corner brackets around the object when selected', () => {
    expect(chooseTargetMarker(input({ majorAxisArcmin: 30 }))).toEqual({
      kind: 'ring', x: 200, y: 150, radiusPx: SMALL_MARKER_RADIUS_PX,
    });
    const brackets = chooseTargetMarker(input({ majorAxisArcmin: 30 }, { isSelected: true }));
    expect(brackets).toMatchObject({ kind: 'brackets', x: 200, y: 150, halfSizePx: 25 });
  });

  /** Zoomed far out, the selected brackets keep their smallest size. */
  it('keeps the selected brackets at least the smallest size', () => {
    const brackets = chooseTargetMarker(input({ majorAxisArcmin: 30 }, { isSelected: true, pixelsPerDeg: 1 }));
    expect(brackets).toMatchObject({ kind: 'brackets', halfSizePx: TARGET_MARKER_MIN_PX / 2 });
  });

  /** A target without a catalog size falls back to its fieldOfView string. */
  it('sizes a target without a catalog size from its field of view', () => {
    expect(targetExtentDegrees({ id: 'A', ra: 1, dec: 1, fieldOfView: '42′' })).toBeCloseTo(0.7);
    expect(targetExtentDegrees({ id: 'A', ra: 1, dec: 1, fieldOfView: '42′', majorAxisArcmin: 12 })).toBeCloseTo(0.2);
    expect(targetExtentDegrees({ id: 'A', ra: 1, dec: 1, majorAxisArcmin: null })).toBeCloseTo(18 / 60);
  });

  /** A target off the visible side of the sky gets no marker. */
  it('draws nothing for a target that is not visible', () => {
    const hidden: ProjectionContext['projectCoords'] = () => ({ x: 0, y: 0, visible: false, alt: -10 });
    expect(chooseTargetMarker(input({}, { projectCoords: hidden, isSelected: true }))).toEqual({ kind: 'none' });
  });
});

describe('catalog shape ellipse', () => {
  const galaxy = { majorAxisArcmin: 30, minorAxisArcmin: 12, positionAngleDeg: 0 };

  /** Position angle 0 points north, which is up on an unturned sky. */
  it('points a position angle of 0 north', () => {
    const marker = chooseTargetMarker(input(galaxy, { isSelected: true }));
    expect(marker.kind).toBe('ellipse');
    if (marker.kind !== 'ellipse') return;
    expect(marker.semiMajorPx).toBeCloseTo(25, 0);
    expect(marker.semiMinorPx).toBeCloseTo(10, 0);
    expect(marker.rotationRad).toBeCloseTo(-Math.PI / 2, 2);
  });

  /** Position angle 90 points east, which is left on an unturned sky. */
  it('points a position angle of 90 east', () => {
    const marker = chooseTargetMarker(input({ ...galaxy, positionAngleDeg: 90 }));
    if (marker.kind !== 'ellipse') throw new Error(`expected an ellipse, got ${marker.kind}`);
    expect(Math.abs(marker.rotationRad)).toBeCloseTo(Math.PI, 2);
  });

  /** When the sky turns on screen, the ellipse turns with it. */
  it('turns with the sky', () => {
    const marker = chooseTargetMarker(input(galaxy, { projectCoords: flatProjection(30) }));
    if (marker.kind !== 'ellipse') throw new Error(`expected an ellipse, got ${marker.kind}`);
    expect(marker.rotationRad).toBeCloseTo(-Math.PI / 2 + Math.PI / 6, 2);
  });

  /** An ellipse too small to see falls back to the small ring; a selected one grows to the smallest size. */
  it('uses the ring for a tiny ellipse and grows a tiny selected one', () => {
    const tiny = { majorAxisArcmin: 1, minorAxisArcmin: 0.5, positionAngleDeg: 45 };
    expect(chooseTargetMarker(input(tiny)).kind).toBe('ring');
    const grown = chooseTargetMarker(input(tiny, { isSelected: true }));
    if (grown.kind !== 'ellipse') throw new Error(`expected an ellipse, got ${grown.kind}`);
    expect(grown.semiMajorPx).toBeCloseTo(TARGET_MARKER_MIN_PX / 2, 3);
    expect(grown.semiMinorPx).toBeCloseTo(TARGET_MARKER_MIN_PX / 4, 3);
  });

  /** Without a minor axis and position angle there is no shape: brackets, as for a cluster. */
  it('draws brackets for a target with only a major axis', () => {
    expect(chooseTargetMarker(input({ majorAxisArcmin: 30 }, { isSelected: true })).kind).toBe('brackets');
  });

  /** The step along the position angle lands in the right direction on the sky. */
  it('steps north and east on the sky', () => {
    const north = offsetOnSky(10, 20, 0, 1);
    expect(north.dec).toBeCloseTo(21, 6);
    expect(north.ra).toBeCloseTo(10, 6);
    const east = offsetOnSky(10, 20, 90, 1);
    expect(east.ra).toBeGreaterThan(10);
  });
});

describe('picture outline', () => {
  /** The outline walks the grid's outer points once around and closes at its start. */
  it('follows the grid edge through the projection', () => {
    const projection = flatProjection();
    const outline = footprintOutline(picture, projection);
    expect(outline).toHaveLength(4 * (picture.gridSize - 1) + 1);
    const expectedIndices = [0, 1, 2, 5, 8, 7, 6, 3, 0];
    expectedIndices.forEach((gridIndex, position) => {
      const [ra, dec] = picture.grid[gridIndex];
      const point = projection(ra, dec);
      expect(outline[position]?.x).toBeCloseTo(point.x, 6);
      expect(outline[position]?.y).toBeCloseTo(point.y, 6);
    });
  });
});

describe('shown picture check', () => {
  /**
   * Builds a projection context with the Images layer.
   *
   * @param {boolean} show Whether the layer is on.
   * @param {boolean} loaded Whether the picture has loaded.
   * @returns {ProjectionContext} The context.
   */
  function layerContext(show: boolean, loaded: boolean): ProjectionContext {
    return {
      width: 400,
      height: 300,
      projectCoords: flatProjection(),
      stackPictures: { show, opacity: 1, pictures: [picture], getDrawable: () => (loaded ? ({} as HTMLCanvasElement) : null) },
    } as unknown as ProjectionContext;
  }
  const target: PlanetariumTarget = { id: 'M 81', ra: 10, dec: 20 };

  /** A picture counts only when the layer is on and the picture has loaded. */
  it('counts a picture only when it is on and loaded', () => {
    const on = layerContext(true, true);
    expect(shownPictureFor(target, false, on, picturesByTarget(on))).toBe(picture);
    const loading = layerContext(true, false);
    expect(shownPictureFor(target, false, loading, picturesByTarget(loading))).toBeNull();
    const off = layerContext(false, true);
    expect(shownPictureFor(target, false, off, picturesByTarget(off))).toBeNull();
  });
});

describe('marker colors', () => {
  afterEach(() => {
    document.documentElement.style.removeProperty('--sky-target-marker');
    document.documentElement.style.removeProperty('--sky-target-marker-selected');
    clearThemeColors();
  });

  /** The overlay strokes the faint token for an unselected target and the bright one for the selected. */
  it('reads the marker colors from the theme tokens', () => {
    clearThemeColors();
    expect(readThemeColor('--sky-target-marker')).toBe(MISSING_TOKEN_COLOR);
    document.documentElement.style.setProperty('--sky-target-marker', 'rgb(1, 2, 3)');
    document.documentElement.style.setProperty('--sky-target-marker-selected', 'rgb(4, 5, 6)');

    const strokes: string[] = [];
    const context = {
      save: vi.fn(), restore: vi.fn(), setLineDash: vi.fn(), beginPath: vi.fn(), arc: vi.fn(),
      moveTo: vi.fn(), lineTo: vi.fn(), ellipse: vi.fn(), lineWidth: 1, strokeStyle: '',
      stroke: vi.fn(function (this: { strokeStyle: string }) { strokes.push(this.strokeStyle); }),
    };
    const projectionContext = {
      width: 400, height: 300, fov: 4, showCatalog: true, selectedTargetId: 'M 81',
      projectCoords: flatProjection(),
      targets: [{ id: 'M 81', ra: 10, dec: 20 }, { id: 'M 82', ra: 10.2, dec: 20.1 }],
    } as unknown as ProjectionContext;

    new TargetOverlay().draw(context as unknown as CanvasRenderingContext2D, projectionContext);

    expect(strokes).toEqual(['rgb(4, 5, 6)', 'rgb(1, 2, 3)']);
    expect(context.arc).toHaveBeenCalledTimes(1);
  });
});

describe('target click test', () => {
  /** A click inside a large target's marker, away from its center, still picks it. */
  it('picks a target anywhere inside its catalog size', () => {
    const target = { id: 'M 31', ra: 10.68, dec: 41.27, majorAxisArcmin: 180, type: 'target' } as unknown as PlanetariumTarget;
    const base = {
      sources: [], targets: [target], showStars: true, showCatalog: true, showEnvironment: false,
      canvasWidth: 800, canvasHeight: 600, fov: 10, lst: 10.68, observerLat: 41.27, centerAlt: 90, centerAz: 0,
    };
    expect(findNearestSource({ ...base, clickX: 400, clickY: 300 })?.id).toBe('M 31');
    const smaller = { ...target, majorAxisArcmin: undefined };
    const farClick = { ...base, clickX: 400 + 80, clickY: 300 };
    expect(findNearestSource({ ...farClick })?.id).toBe('M 31');
    expect(findNearestSource({ ...farClick, targets: [smaller] })).toBeNull();
  });
});
