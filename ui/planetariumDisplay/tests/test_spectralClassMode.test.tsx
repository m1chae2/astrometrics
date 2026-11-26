/**
 * @fileoverview Tests of the Planetarium's "By spectral class" mode and its left column fold.
 *
 * In the spectral class mode the sky map's own stars are every star of the class lists, each
 * colored by class (our own spectrum's type, else the catalog type, else the B-V color), with a
 * color legend; picking a class keeps only that class. The card and framing panel share one
 * column. "By target" is unchanged. The left column
 * folds to a strip like the Astronomy Manager's. The sky map and most data hooks are fakes.
 */

import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { classFromBMinusV, starColorClass } from '../utils/starColor';

const mapProps: { current: Record<string, any> } = { current: {} };
let skyMapMounts = 0;
const callBackendMock = vi.fn();

vi.mock('../components/CelestialSkyMap', async () => {
  const ReactModule = await import('react');
  return {
    CelestialSkyMap: (props: Record<string, any>) => {
      mapProps.current = props;
      ReactModule.useEffect(() => { skyMapMounts += 1; }, []);
      return <div data-testid="sky-map" />;
    },
  };
});
vi.mock('../components/PlanetariumInfoCard', () => ({
  PlanetariumInfoCard: (props: { onToggleFraming?: () => void; isFramingOpen?: boolean }) => (
    <div data-testid="info-card" data-framing-open={String(!!props.isFramingOpen)} onClick={props.onToggleFraming}>card</div>
  ),
}));
vi.mock('../components/FramingPanel', () => ({
  FramingPanel: ({ isOpen }: { isOpen: boolean }) => (isOpen ? <div data-testid="framing">framing</div> : null),
}));
vi.mock('../components/AlignmentPointCard', () => ({ AlignmentPointCard: () => null }));
vi.mock('../components/PlanetariumToolbar', () => ({ PlanetariumToolbar: () => null }));
vi.mock('../components/PlanetariumContextMenu', () => ({ PlanetariumContextMenu: () => null }));
vi.mock('../components/PlanetariumDateTimeModal', () => ({ PlanetariumDateTimeModal: () => null }));
vi.mock('../components/DeepCatalogPrompt', () => ({ DeepCatalogPrompt: () => null }));
vi.mock('../../common/context/PlanningTimeContext', () => ({
  usePlanningTime: () => ({ planningTime: null, setPlanningTime: vi.fn(), returnToNow: vi.fn() }),
}));
vi.mock('../hooks/useConstellationLines', () => ({ useConstellationLines: () => ({ lines: [] }) }));
vi.mock('../hooks/useObserverLocation', () => ({ useObserverLocation: () => ({ location: null }) }));
vi.mock('../hooks/useOverlayToggles', () => ({
  useOverlayToggles: () => ({
    showStars: true, setShowStars: vi.fn(), showFOVOutline: true, setShowFOVOutline: vi.fn(),
    showEnvironment: true, setShowEnvironment: vi.fn(), showGrid: true, setShowGrid: vi.fn(),
    showCatalog: true, setShowCatalog: vi.fn(), showConstellations: true, setShowConstellations: vi.fn(),
    showTelescope: true, setShowTelescope: vi.fn(),
  }),
}));
vi.mock('../../common/hooks/useEquipmentConfiguration', () => ({ useEquipmentConfiguration: () => ({ configuration: null }) }));
vi.mock('../hooks/useAlignmentSessionData', () => ({
  useAlignmentSessionData: () => ({
    availableSessions: [], selectedSessionId: null, setSelectedSessionId: vi.fn(),
    activeAlignmentTargets: [], activePolarAlignment: null,
  }),
}));
vi.mock('../hooks/useTrackingRiskMap', () => ({ useTrackingRiskMap: () => ({ trackingRisk: null }) }));

/** A background (Hipparcos) G star and a library star with no spectrum, as the sky map gets them. */
const backgroundStar = {
  id: 'HIP 1', name: 'HIP 1', ra: 10, dec: 10, type: 'star', catalogSource: 'hipparcos',
  spectralType: 'G2V', magnitude: 4, hasSpectra: false, hasPhotometry: false,
};
const libraryStarWithoutSpectrum = {
  id: 'FIELD_J1', name: 'FIELD_J1', ra: 11, dec: 11, type: 'star', spectralType: 'K0', hasSpectra: false, hasPhotometry: false,
};
const viewportStars = [backgroundStar, libraryStarWithoutSpectrum];
const viewportTargets = [{ id: 'M 31', ra: 10.7, dec: 41.3 }];
vi.mock('../hooks/useSourceMerging', () => ({
  useSourceMerging: () => ({ stars: viewportStars, targets: viewportTargets, deepCatalogStatus: null, libraryTargets: [] }),
}));
vi.mock('../../common/services/backendApi', () => ({
  callBackend: (...args: unknown[]) => callBackendMock(...args),
}));
vi.mock('../../common/context/TargetContext', () => ({
  useTargetContext: () => ({
    selectedTarget: '', setSelectedTarget: vi.fn(), pendingTarget: '', setPendingTarget: vi.fn(), reloadKey: 0,
  }),
}));
vi.mock('../../common/hooks/useTargetListLogic', () => ({
  useTargetListLogic: () => ({ targets: [], stars: [], highlightedIds: new Set<string>() }),
}));
vi.mock('../../common/context/RemoteStatusContext', () => ({ useRemoteStatusContext: () => ({ remoteTargets: [] }) }));
vi.mock('../../common/hooks/useTelescopeStatus', () => ({
  useTelescopeStatus: () => ({ telescopeConnection: null, telemetry: null }),
}));
vi.mock('../../astronomyManager/hooks/useTargetBrowserItems', () => ({
  useTargetBrowserItems: () => ({
    items: [{ id: 'M 31', value: 'M 31', label: 'M 31' }],
    isLoading: false, isError: false, retry: vi.fn(), filterText: '', setFilterText: vi.fn(),
  }),
}));
vi.mock('../../astronomyManager/hooks/useSpectralClassBrowserItems', () => ({
  ALL_SPECTRAL_CLASSES_VALUE: 'All',
  useSpectralClassBrowserItems: () => ({
    items: [{ id: 'All', value: 'All', label: 'All' }, { id: 'K', value: 'K', label: 'K — Orange' }],
    isLoading: false, isError: false, retry: vi.fn(), filterText: '', setFilterText: vi.fn(),
  }),
}));
const emptyStarList = {
  items: [], isLoading: false, isError: false, retry: vi.fn(), filterText: '', setFilterText: vi.fn(),
  page: 0, setPage: vi.fn(), hasMore: false, totalPages: 0,
};
vi.mock('../../astronomyManager/hooks/useSpectrumList', () => ({ useSpectrumList: () => emptyStarList }));
vi.mock('../../astronomyManager/hooks/useStarsBySpectralClassList', () => ({ useStarsBySpectralClassList: () => emptyStarList }));
vi.mock('../../common/utils/appBootReadiness', () => ({ useReportModeReady: vi.fn() }));

import { PlanetariumDisplay } from '../PlanetariumDisplay';

/** The stars with spectra the fake backend returns. */
const spectrumRows = [
  // Our own spectrum says K, the catalog says G: our type wins.
  { id: 'Own K', name: 'Own K', ra: 1, dec: 1, magnitude: 3, hasCatalogMagnitude: true, spectralType: 'G8III', ownSpectralType: 'K1III', bMinusV: 0.7, hasSpectra: true, hasPhotometry: false },
  // No own type: the catalog type (K) is used.
  { id: 'Catalog K', name: 'Catalog K', ra: 2, dec: 2, magnitude: 4, hasCatalogMagnitude: true, spectralType: 'K5V', ownSpectralType: 'Unknown', bMinusV: null, hasSpectra: true, hasPhotometry: true },
  // No type at all: the B-V color (1.6) gives M.
  { id: 'Color only', name: 'Color only', ra: 3, dec: 3, magnitude: 5, hasCatalogMagnitude: true, spectralType: '', ownSpectralType: '', bMinusV: 1.6, hasSpectra: true, hasPhotometry: false },
  // A carbon star from the class list, with no spectrum: its catalog type gives the carbon red.
  { id: 'Case 36', name: 'Case 36', ra: 5, dec: 5, magnitude: 9, hasCatalogMagnitude: true, spectralType: 'C5:,4', ownSpectralType: '', bMinusV: null, hasSpectra: false, hasPhotometry: true },
  // Nothing known: gray.
  { id: 'Unknown', name: 'Unknown', ra: 4, dec: 4, magnitude: 6, hasCatalogMagnitude: true, spectralType: '', ownSpectralType: '', bMinusV: null, hasSpectra: true, hasPhotometry: false },
];

/**
 * Renders the Planetarium in a fresh query client.
 * @returns The user-event handle.
 */
function renderPlanetarium() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <PlanetariumDisplay />
    </QueryClientProvider>,
  );
  return { user: userEvent.setup(), view };
}

/**
 * The ids and color classes of the stars handed to the sky map.
 * @returns One "id:colorClass" entry per star ("-" when it has none).
 */
function mapStars(): string[] {
  return (mapProps.current.sources as Array<{ id: string; colorClass?: string }>).map(
    (star) => `${star.id}:${star.colorClass ?? '-'}`,
  );
}

beforeEach(() => {
  localStorage.clear();
  mapProps.current = {};
  skyMapMounts = 0;
  callBackendMock.mockReset();
  callBackendMock.mockImplementation(async (method: string) => {
    if (method === 'planetarium:get_class_stars') return spectrumRows;
    return [];
  });
});

describe('star color class', () => {
  /** Our own type comes first, then the catalog type, then the B-V color, then none. */
  it('prefers our own type over the catalog type over the color', () => {
    expect(starColorClass({ ownSpectralType: 'K1III', spectralType: 'G8III', bMinusV: 0.1 })).toBe('K');
    expect(starColorClass({ ownSpectralType: 'Unknown', spectralType: 'A0V', bMinusV: 1.6 })).toBe('A');
    expect(starColorClass({ ownSpectralType: '', spectralType: '', bMinusV: 0.65 })).toBe('G');
    expect(starColorClass({ ownSpectralType: '', spectralType: '', bMinusV: null })).toBe('none');
    // Carbon-type (C, R, N) and S-type stars take the carbon red; Wolf-Rayet stars their blue.
    expect(['C5:,4', 'C6,4e', 'R', 'N', 'S4,7e'].map((type) => starColorClass({ spectralType: type }))).toEqual(['C', 'C', 'C', 'C', 'C']);
    expect(starColorClass({ spectralType: 'WR' })).toBe('W');
  });

  /** B-V maps to the standard main-sequence classes. */
  it('maps B-V to the standard classes', () => {
    expect([-0.33, -0.1, 0.1, 0.4, 0.65, 1.0, 1.6].map(classFromBMinusV)).toEqual(['O', 'B', 'A', 'F', 'G', 'K', 'M']);
  });
});

describe('Planetarium spectral class mode', () => {
  /** By target: the sky map gets the viewport stars unchanged, with no colors, no legend and no request. */
  it('leaves By target as it was', async () => {
    renderPlanetarium();
    await waitFor(() => expect(mapProps.current.sources).toBeDefined());
    expect(mapProps.current.sources).toBe(viewportStars);
    expect(mapProps.current.targets).toBe(viewportTargets);
    expect(screen.queryByRole('group', { name: 'Star colors by spectral class' })).toBeNull();
    expect(callBackendMock.mock.calls.some(([method]) => method === 'planetarium:get_class_stars')).toBe(false);
  });

  /** By spectral class: only the class-list stars, colored by class, no background stars or targets, and the legend. */
  it('shows only the class stars, colored by class, with a legend', async () => {
    const { user } = renderPlanetarium();
    await user.click(screen.getByRole('tab', { name: 'By spectral class' }));
    await waitFor(() => expect(mapStars()).toEqual([
      'Own K:K', 'Catalog K:K', 'Color only:M', 'Case 36:C', 'Unknown:none',
    ]));
    expect(mapProps.current.targets).toEqual([]);
    const legend = screen.getByRole('group', { name: 'Star colors by spectral class' });
    expect(Array.from(legend.querySelectorAll('[data-color-class]')).map((entry) => entry.getAttribute('data-color-class')))
      .toEqual(['O', 'B', 'A', 'F', 'G', 'K', 'M', 'C', 'W', 'none']);
    expect(legend.textContent).toContain('5 stars by class');
  });

  /** Picking a class keeps only that catalog class's stars. */
  it('filters to the chosen class', async () => {
    const { user } = renderPlanetarium();
    await user.click(screen.getByRole('tab', { name: 'By spectral class' }));
    await waitFor(() => expect(mapStars()).toHaveLength(5));
    await user.click(await screen.findByText('K — Orange'));
    await waitFor(() => expect(mapStars()).toEqual(['Catalog K:K']));
    // A class this small is drawn whole at any zoom.
    expect(mapProps.current.sources[0].alwaysShow).toBe(true);
    expect(screen.getByRole('group', { name: 'Star colors by spectral class' }).textContent).toContain('1 star in class K');
  });

  /** Going back to By target gives the old stars and hides the legend. */
  it('returns to the By target view', async () => {
    const { user } = renderPlanetarium();
    await user.click(screen.getByRole('tab', { name: 'By spectral class' }));
    await waitFor(() => expect(mapStars()).toHaveLength(5));
    await user.click(screen.getByRole('tab', { name: 'By target' }));
    expect(mapProps.current.sources).toBe(viewportStars);
    expect(mapProps.current.targets).toBe(viewportTargets);
    expect(screen.queryByRole('group', { name: 'Star colors by spectral class' })).toBeNull();
  });
});

describe('star drawing rule', () => {
  /** Library stars follow the zoom: a wide view shows only bright ones, a narrow view fainter ones too. */
  it('shows more library stars as the view narrows, and always the selected one', async () => {
    const { isDisplayableStar, computeLimitingMagnitude } = await import('../layers/StarOverlay');
    const bright = { id: 'bright', ra: 10, dec: 10, type: 'star', magnitude: 6.4, hasCatalogMagnitude: true };
    const faint = { id: 'faint', ra: 10, dec: 10, type: 'star', magnitude: 15.0, hasCatalogMagnitude: true };
    const noMagnitude = { id: 'none', ra: 10, dec: 10, type: 'star' };
    const wide = 14.7;
    const narrow = 0.6;
    expect(isDisplayableStar(bright, true, true, computeLimitingMagnitude(wide), wide)).toBe(true);
    expect(isDisplayableStar(faint, true, true, computeLimitingMagnitude(wide), wide)).toBe(false);
    expect(isDisplayableStar(noMagnitude, true, true, computeLimitingMagnitude(wide), wide)).toBe(false);
    expect(isDisplayableStar(faint, true, true, computeLimitingMagnitude(narrow), narrow)).toBe(true);
    expect(isDisplayableStar(noMagnitude, true, true, computeLimitingMagnitude(narrow), narrow)).toBe(true);
    // The selected star shows at any zoom.
    expect(isDisplayableStar(faint, true, true, computeLimitingMagnitude(wide), wide, 'faint')).toBe(true);
  });

  /** A colored star follows the zoom too, unless it belongs to a small chosen class; it is drawn bigger and bright. */
  it('lets a small class show whole and draws colored stars bigger and brighter', async () => {
    const { isDisplayableStar, computeSourceBrightness, starMarkerRadius, STAR_MARKER_RADIUS_PX, CLASS_STAR_MIN_BRIGHTNESS } =
      await import('../layers/StarOverlay');
    const faintColored = { ra: 10, dec: 10, type: 'star', magnitude: 14, hasCatalogMagnitude: true, colorClass: 'K' };
    expect(isDisplayableStar(faintColored, false, false, 6, 90)).toBe(false);
    expect(isDisplayableStar({ ...faintColored, alwaysShow: true }, false, false, 6, 90)).toBe(true);
    // The Deep Catalog switch does not hide a listed star that the zoom allows.
    expect(isDisplayableStar({ ...faintColored, magnitude: 5 }, false, false, 6, 90)).toBe(true);
    expect(computeSourceBrightness(faintColored, 90)).toBeGreaterThanOrEqual(CLASS_STAR_MIN_BRIGHTNESS);
    expect(starMarkerRadius(faintColored)).toBeGreaterThan(STAR_MARKER_RADIUS_PX);
    expect(starMarkerRadius({})).toBe(STAR_MARKER_RADIUS_PX);
  });
});

describe('Planetarium card and framing column', () => {
  /** The card and the framing panel are in one column, card first, so they never cover each other. */
  it('opens framing under the card in the same column', async () => {
    renderPlanetarium();
    await waitFor(() => expect(mapProps.current.onSelectSource).toBeDefined());
    act(() => mapProps.current.onSelectSource({ id: 'Vega', name: 'Vega', ra: 279.2, dec: 38.8, type: 'star', hasSpectra: true, hasPhotometry: false }));
    const card = await screen.findByTestId('info-card');
    fireEvent.click(card);
    const framing = await screen.findByTestId('framing');
    const column = screen.getByTestId('planetarium-side-stack');
    expect(Array.from(column.children)).toEqual([card, framing]);
    // The card keeps its details while framing extends below it in the same column.
    expect(card.getAttribute('data-framing-open')).toBe('true');
  });
});

describe('Planetarium left column fold', () => {
  /** The column folds to a strip naming the target, opens again, and the sky map stays mounted. */
  it('folds to a strip naming the selected target, and opens again', async () => {
    const { user } = renderPlanetarium();
    const hide = await screen.findByTitle('Hide target list');
    await user.click(hide);

    const strip = screen.getByRole('button', { name: 'Targets, M 31' });
    expect(strip.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByRole('tab', { name: 'By spectral class' })).toBeNull();
    // The layout narrows its left column while a strip is in it (collapsibleColumn.css), so the sky
    // takes the width; the sky map is not rebuilt, and its render loop follows its own size.
    const layout = document.querySelector('.layout');
    expect(layout?.querySelector('.collapsible-column--collapsed')).not.toBeNull();
    expect(screen.getByTestId('sky-map')).toBeTruthy();
    expect(skyMapMounts).toBe(1);

    await user.click(strip);
    expect(screen.getByRole('tab', { name: 'By spectral class' })).toBeTruthy();
    expect(document.querySelector('.collapsible-column--collapsed')).toBeNull();
    expect(skyMapMounts).toBe(1);
  });

  /** The folded state is remembered under the Planetarium's own key. */
  it('remembers the folded state under its own key', async () => {
    const first = renderPlanetarium();
    await first.user.click(await screen.findByTitle('Hide target list'));
    expect(localStorage.getItem('astrometrics:planetarium:targetListCollapsed')).not.toBeNull();
    expect(localStorage.getItem('astrometrics:astronomyManager:targetListCollapsed')).toBeNull();
    first.view.unmount();
    renderPlanetarium();
    expect(screen.getByRole('button', { name: 'Targets, M 31' })).toBeTruthy();
  });
});
