/**
 * @module planetariumDisplay
 * @fileoverview Root container component for the Planetarium display panel.
 *
 * Coordinates the CelestialSkyMap canvas renderer, the left-panel RadioListManager
 * target list, the floating PlanetariumInfoCard, and scientific plot panels.
 * Manages observer date/time state and the three-priority source lookup used by
 * handleSelectObject.
 *
 */

import React, { useState, useCallback, useMemo, useEffect } from 'react';
import { GenericDisplayLayout } from '../common/components/GenericDisplayLayout';
import { RadioListManager } from '../common/radioList/RadioListManager';
import { CelestialSkyMap } from './components/CelestialSkyMap';
import { PlanetariumInfoCard } from './components/PlanetariumInfoCard';
import { AlignmentPointCard } from './components/AlignmentPointCard';
import { PlanetariumToolbar } from './components/PlanetariumToolbar';
import { PlanetariumContextMenu } from './components/PlanetariumContextMenu';
import { PlanetariumDateTimeModal } from './components/PlanetariumDateTimeModal';
import { FramingPanel } from './components/FramingPanel';
import type { FramingPanelCorners } from './layers/FramingOverlay';
import { usePlanningTime } from '../common/context/PlanningTimeContext';
import { useConstellationLines } from './hooks/useConstellationLines';
import { useObserverLocation } from './hooks/useObserverLocation';
import { useTargetsBelowHorizon, normalizeTargetName } from './hooks/useTargetsBelowHorizon';
import { buildSkyVisibleTargetKeys, filterSkyPictures, isSkyTargetShown } from './utils/skyTargetFilter';
import { useOverlayToggles } from './hooks/useOverlayToggles';
import { useEquipmentConfiguration } from '../common/hooks/useEquipmentConfiguration';
import { useAlignmentSessionData } from './hooks/useAlignmentSessionData';
import { useTrackingRiskMap } from './hooks/useTrackingRiskMap';
import { useSourceMerging } from './hooks/useSourceMerging';
import { PlanetariumSource } from '../common/types/planetariumTypes';
import { callBackend } from '../common/services/backendApi';
import { useTargetContext } from '../common/context/TargetContext';
import { useTargetListLogic } from '../common/hooks/useTargetListLogic';
import { useRemoteStatusContext } from '../common/context/RemoteStatusContext';
import { useTelescopeStatus } from '../common/hooks/useTelescopeStatus';
import { safeParse } from './utils/coordinateUtils';
import { DeepCatalogPrompt } from './components/DeepCatalogPrompt';
import { StarColorLegend } from './components/StarColorLegend';
import { useClassStarSources } from './hooks/useClassStarSources';
import { useSideStackTop } from './hooks/useSideStackTop';
import { useStackPictures } from './hooks/useStackPictures';
import { completeStarFromLibrary } from './utils/libraryStarCard';
import { consumeHandoff, usePendingHandoff, useHandoffConsumer } from '../common/utils/displayHandoffStore';
import { useTargetBrowserItems } from '../astronomyManager/hooks/useTargetBrowserItems';
import { QueryClientContext } from '@tanstack/react-query';
import { prefetchAstronomyList } from '../common/queries/useAstronomyListQuery';
import { useSpectralClassBrowserItems, ALL_SPECTRAL_CLASSES_VALUE } from '../astronomyManager/hooks/useSpectralClassBrowserItems';
import { useSpectrumList } from '../astronomyManager/hooks/useSpectrumList';
import { useStarsBySpectralClassList } from '../astronomyManager/hooks/useStarsBySpectralClassList';
import { spectralClassLetter } from '../common/utils/starDisplayFormat';
import { useReportModeReady } from '../common/utils/appBootReadiness';
import '../common/styles/segmentedToggle.css';
import { Tabs } from '../common/components/Tabs';
import { Checkbox } from '../common/components/Checkbox';
import { Button } from '../common/components/Button';
import { PopoverSelect, type PopoverSelectOption } from '../common/components/PopoverSelect';
import type { SelectableItem } from '../common/components/SelectableList';
import {
  CAMERA_ALL, CATALOG_ALL, CATALOG_MESSIER, CATALOG_NGC_IC, CATALOG_NO_IMAGE, CATALOG_PLANETS, CATALOG_STARS,
  SORT_ALPHABETICAL, SORT_NEWEST, type SortChoice,
} from '../common/hooks/targetListFiltering';
import { CollapsibleColumn, useCollapsibleColumn } from '../common/components/CollapsibleColumn';
import './styles/planetariumDisplay.css';

// Browser storage key for the "Only targets above the horizon" choice. It is a
// per-viewer convenience, so the list still works when storage is blocked.
const ABOVE_HORIZON_ONLY_STORAGE_KEY = 'planetarium.targetList.aboveHorizonOnly';

/**
 * Reads the saved "Only targets above the horizon" choice.
 *
 * @returns {boolean} True when the viewer last turned the filter on; false when it is off or storage cannot be read.
 */
const readAboveHorizonOnly = (): boolean => {
  try {
    return window.localStorage.getItem(ABOVE_HORIZON_ONLY_STORAGE_KEY) === 'true';
  } catch {
    return false;
  }
};

// Page size of a target's star list; the same as useSpectrumList's, so a
// prefetch fills the page the list will ask for.
const STAR_LIST_PAGE_SIZE = 100;

// Short names for the shared target filter's catalogs, for the compact "Show" picker.
const CATALOG_SHORT_LABELS: Record<string, string> = {
  [CATALOG_ALL]: 'All targets',
  [CATALOG_MESSIER]: 'Messier',
  [CATALOG_NGC_IC]: 'NGC IC',
  [CATALOG_STARS]: 'Stars',
  [CATALOG_PLANETS]: 'Planets',
  [CATALOG_NO_IMAGE]: 'No image',
};

// Id prefixes that tell a catalog entry from a camera entry in the "Show" picker,
// and the id of the extra entry shown when both a catalog and a camera are set.
const SHOW_CATALOG_PREFIX = 'catalog:';
const SHOW_CAMERA_PREFIX = 'camera:';
const SHOW_PAIR_ID = 'pair';

// The "Sort" picker's choices.
const TARGET_SORT_OPTIONS: PopoverSelectOption[] = [
  { id: SORT_NEWEST, label: 'Newest', title: 'Most recently imaged first' },
  { id: SORT_ALPHABETICAL, label: 'A–Z', title: 'By name' },
];

// Longest wait, in milliseconds, for the column's slide to end before the
// work held back for it runs anyway (the slide itself takes 240 ms).
const SLIDE_END_FALLBACK_MS = 400;

// Field of view, in degrees, the sky map zooms to when a star is picked from
// the star list or handed over from the Astronomy Manager. Wide enough to
// show the star's neighbours, tight enough that the star is easy to find.
const STAR_PICK_FOV_DEG = 1.0;

/**
 * Root panel component for the Planetarium Display.
 *
 * Renders a three-column GenericDisplayLayout: a target search list on the left,
 * an interactive CelestialSkyMap in the center, and floating overlay cards for
 * object details and scientific plot panels.
 *
 * @func PlanetariumDisplay
 * @returns {React.ReactElement} The rendered planetarium layout.
 */
export const PlanetariumDisplay: React.FC = () => {
  const [selectedSource, setSelectedSource] = useState<PlanetariumSource | null>(null);
  const [contextSource, setContextSource] = useState<PlanetariumSource | null>(null);
  const [contextPos, setContextPos] = useState<{ x: number; y: number } | null>(null);

  // Bumped only by an explicit pick from the target list, never by a canvas
  // click, so CelestialSkyMap can tell the two apart: a list pick slews the
  // camera to the target, a canvas click just selects it in place.
  const [slewRequestId, setSlewRequestId] = useState<number>(0);
  // Whether the pending slew should also turn tracking on. Set in the same
  // update that bumps slewRequestId, so the map sees the two together.
  const [trackOnSlew, setTrackOnSlew] = useState<boolean>(false);

  // Get telescope connection and telemetry
  const { telescopeConnection, telemetry } = useTelescopeStatus();

  // All overlay and plot visibility toggles
  const {
    showStars, setShowStars,
    showFOVOutline, setShowFOVOutline,
    showEnvironment, setShowEnvironment,
    showGrid, setShowGrid,
    showCatalog, setShowCatalog,
    showConstellations, setShowConstellations,
    showTelescope, setShowTelescope,
  } = useOverlayToggles();

  // Mount tracking risk heatmap toggle state
  const [showTrackingRisk, setShowTrackingRisk] = useState<boolean>(false);

  // Historical session review and cumulative tracking data
  const {
    availableSessions,
    selectedSessionId,
    setSelectedSessionId,
    activeAlignmentTargets,
    activePolarAlignment,
  } = useAlignmentSessionData(telemetry);
  const { trackingRisk } = useTrackingRiskMap(showTrackingRisk);

  // The Images layer: each target's stack picture laid on the sky. It is its own layer, so it
  // shows in both browse modes while its switch is on.
  const stackPictureLayer = useStackPictures();

  const [viewerCenter, setViewerCenter] = useState<{ ra: number; dec: number } | null>(null);
  const [currentFOV, setCurrentFOV] = useState<number>(90.0);
  // Corners of the framing panels the sky map outlines (set by the framing panel).
  const [framingPanels, setFramingPanels] = useState<FramingPanelCorners[]>([]);
  // The framing panel opens from the Framing button on the selected object's info card.
  const [isFramingOpen, setIsFramingOpen] = useState<boolean>(false);
  const framingButtonRef = React.useRef<HTMLButtonElement>(null);
  // The card column starts at the top-left inset, or just below the toolbar when a narrow window
  // makes the toolbar reach over it.
  const viewerContainerRef = React.useRef<HTMLDivElement>(null);
  const sideStackRef = React.useRef<HTMLDivElement>(null);
  const sideStackTop = useSideStackTop(viewerContainerRef, sideStackRef);
  // Set when the panel's Close is pressed, so focus goes back to the card's Framing button
  // once the full card (and that button) is shown again.
  const returnFocusToFramingButton = React.useRef(false);
  /** Closes the framing panel; focus then returns to the card's Framing button. */
  const closeFraming = useCallback(() => {
    returnFocusToFramingButton.current = true;
    setIsFramingOpen(false);
  }, []);
  useEffect(() => {
    if (!isFramingOpen && returnFocusToFramingButton.current) {
      returnFocusToFramingButton.current = false;
      framingButtonRef.current?.focus();
    }
  }, [isFramingOpen]);

  // Date and Time controls. The time is the shared planning time: picking a
  // date here fixes it for every planning display, and it follows changes made
  // elsewhere. Playback (the play button and slider on the sky map) is a local
  // animation on top of it and is never published.
  const [isTimeModalOpen, setIsTimeModalOpen] = useState<boolean>(false);
  const { planningTime, setPlanningTime, returnToNow } = usePlanningTime();
  // When the planning time is Now, the sky follows the system clock. Nothing
  // ticks in React state for this: a tick re-rendered this whole display (and
  // restarted the sky map's render loop) every second. The sky map reads the
  // clock itself.
  const isLiveTime = planningTime === null;
  const currentDate = useMemo(() => (planningTime === null ? new Date() : new Date(planningTime)), [planningTime]);

  // The date the time dialog starts from: now (when it opens) in live mode.
  const dialogDate = useMemo(
    () => (isLiveTime ? new Date() : currentDate),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refreshed each time the dialog opens
    [isLiveTime, currentDate, isTimeModalOpen],
  );

  const handleDateChange = useCallback((date: Date) => {
    setPlanningTime(date);
  }, [setPlanningTime]);

  const handleResetToNow = useCallback(() => {
    returnToNow();
  }, [returnToNow]);

  // Center RA/Dec parsed cleanly to numbers (degrees)
  const raValue = useMemo(() => safeParse(viewerCenter?.ra), [viewerCenter?.ra]);
  const decValue = useMemo(() => safeParse(viewerCenter?.dec), [viewerCenter?.dec]);

  // Bundled constellation stick-figure lines: fetched once (no ra/dec/radius —
  // the whole dataset is static and small) rather than re-queried on pan/zoom.
  const { lines: constellationLines } = useConstellationLines(showConstellations);

  const { stars, targets, deepCatalogStatus, libraryTargets } = useSourceMerging(
    raValue, decValue, currentFOV, showStars,
  );

  const { location, loading: isLocationLoading } = useObserverLocation();
  const { configuration: equipmentConfig } = useEquipmentConfiguration();

  const {
    selectedTarget: selectedTargetId,
    setSelectedTarget: setSelectedTargetId,
    pendingTarget,
    setPendingTarget,
    reloadKey
  } = useTargetContext();

  const { remoteTargets } = useRemoteStatusContext();
  const targetList = useTargetListLogic(
    reloadKey,
    pendingTarget,
    selectedTargetId,
    setPendingTarget,
    setSelectedTargetId,
    remoteTargets,
    false,
    true
  );

  const selectedTarget = useMemo(() => {
    return libraryTargets.find(target => target.id === selectedTargetId) || null;
  }, [libraryTargets, selectedTargetId]);

  // Same target/spectral-class split navigation as the Astronomy Manager:
  // browse by target and drill into its stars, or browse by catalog
  // spectral classification with stars ranked by self-determined match
  // quality. `scopedTarget`/`scopedSpectralClass` is which primary-list
  // entry is expanded in the secondary star list -- independent of
  // `selectedTargetId`, which is whatever object (a target or one of its
  // stars) is actually centered/selected on the sky map right now.
  const [navigationMode, setNavigationMode] = useState<'target' | 'spectralClass'>('target');
  const [scopedTarget, setScopedTarget] = useState<string>('');
  const [scopedSpectralClass, setScopedSpectralClass] = useState<string>(ALL_SPECTRAL_CLASSES_VALUE);

  // Drill-in: each browse mode shows either its list (level 1) or the stars
  // of the picked entry (level 2). Each mode remembers its own level.
  const [drilledInModes, setDrilledInModes] = useState<Record<'target' | 'spectralClass', boolean>>({
    target: false,
    spectralClass: false,
  });
  const isDrilledIn = drilledInModes[navigationMode];
  const levelOnePaneRef = React.useRef<HTMLDivElement>(null);
  const backButtonRef = React.useRef<HTMLButtonElement>(null);
  // Where to put the focus after the next level change, so it never stays on
  // a list that has just slid away. A keyboard action focuses a control (the
  // Back button or the picked row); a mouse pick focuses the level 2 box
  // itself, which shows no focus ring but still hears Escape.
  const pendingFocusRef = React.useRef<'back' | 'list' | 'levelTwo' | null>(null);
  const levelTwoPaneRef = React.useRef<HTMLDivElement>(null);

  useEffect(() => {
    const focusTarget = pendingFocusRef.current;
    if (!focusTarget) return;
    pendingFocusRef.current = null;
    // preventScroll: the pane is still sliding in, and the browser would
    // otherwise scroll the clipped column sideways to show the focused control.
    if (focusTarget === 'back') {
      backButtonRef.current?.focus({ preventScroll: true });
    } else if (focusTarget === 'levelTwo') {
      levelTwoPaneRef.current?.focus({ preventScroll: true });
    } else {
      levelOnePaneRef.current?.querySelector<HTMLInputElement>('input[type="radio"]:checked')?.focus({ preventScroll: true });
    }
  }, [isDrilledIn, navigationMode]);

  /**
   * Turns level 2 on or off for one browse mode.
   *
   * @param {'target' | 'spectralClass'} mode - The browse mode.
   * @param {boolean} drilledIn - True to show the star list, false to show the list.
   */
  const setModeDrilledIn = useCallback((mode: 'target' | 'spectralClass', drilledIn: boolean) => {
    setDrilledInModes((previous) => (previous[mode] === drilledIn ? previous : { ...previous, [mode]: drilledIn }));
  }, []);

  // True during an arrow-key press in a level 1 list; see handleLevelOneKeyDown.
  const isArrowKeyPickRef = React.useRef<boolean>(false);

  /**
   * Runs `work` once the column's slide has ended, so the slide gets the main
   * thread to itself; heavy work (centering the sky map) waits for it. With
   * reduced motion there is no slide, so it runs on the next frame.
   *
   * @param {() => void} work - The work to run.
   */
  const runAfterSlide = useCallback((work: () => void) => {
    const pane = levelTwoPaneRef.current;
    const reduceMotion = typeof window.matchMedia === 'function'
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!pane || reduceMotion) {
      window.requestAnimationFrame(() => work());
      return;
    }
    let hasRun = false;
    const runOnce = () => {
      if (hasRun) return;
      hasRun = true;
      pane.removeEventListener('transitionend', handleTransitionEnd);
      window.clearTimeout(fallbackTimer);
      work();
    };
    // Only the pane's own slide counts; a transition inside it (a row's
    // hover color, say) also sends a transitionend that bubbles up here.
    const handleTransitionEnd = (event: TransitionEvent) => {
      if (event.target === pane && event.propertyName === 'transform') runOnce();
    };
    const fallbackTimer = window.setTimeout(runOnce, SLIDE_END_FALLBACK_MS);
    pane.addEventListener('transitionend', handleTransitionEnd);
  }, []);

  // The panes get `will-change: transform` only while they slide, so the
  // browser keeps them on their own layer for the slide and frees it after.
  const hasRenderedDrillRef = React.useRef<boolean>(false);
  useEffect(() => {
    if (!hasRenderedDrillRef.current) {
      hasRenderedDrillRef.current = true;
      return undefined;
    }
    const panes = [levelOnePaneRef.current, levelTwoPaneRef.current].filter(
      (pane): pane is HTMLDivElement => pane !== null,
    );
    panes.forEach((pane) => { pane.style.willChange = 'transform'; });
    const clearWillChange = () => panes.forEach((pane) => { pane.style.willChange = ''; });
    const timer = window.setTimeout(clearWillChange, SLIDE_END_FALLBACK_MS);
    return () => {
      window.clearTimeout(timer);
      clearWillChange();
    };
  }, [isDrilledIn]);


  const targetBrowser = useTargetBrowserItems();
  const spectralClassBrowser = useSpectralClassBrowserItems();
  // The sky map's own catalog sources (useSourceMerging) don't expose a
  // loading flag; the target/star browser sidebar's does and fetches on the
  // same mount, so it's a reasonable proxy for "this view has real data."
  useReportModeReady('Planetarium', !targetBrowser.isLoading);

  // Which targets are under the horizon at the planning time (null until the
  // site is known). Drives the muted "Below" badge and the horizon filter.
  const horizonCheck = useTargetsBelowHorizon(libraryTargets, location, !isLocationLoading, planningTime);
  const [isAboveHorizonOnly, setIsAboveHorizonOnly] = useState<boolean>(readAboveHorizonOnly);

  /**
   * Turns the "Only targets above the horizon" filter on or off and saves the choice for this viewer.
   *
   * @param {React.ChangeEvent<HTMLInputElement>} event - The checkbox change.
   */
  const handleAboveHorizonOnlyChange = useCallback((event: React.ChangeEvent<HTMLInputElement>) => {
    const isChecked = event.target.checked;
    setIsAboveHorizonOnly(isChecked);
    try {
      window.localStorage.setItem(ABOVE_HORIZON_ONLY_STORAGE_KEY, String(isChecked));
    } catch {
      // Storage is blocked; the choice lasts until the app closes.
    }
  }, []);

  // The shared target filter (By catalog, By camera, A–Z or Newest), the same
  // one Image Processing shows in its Filter Targets panel. Its choices are
  // shared by every screen. `targetList.items` is the library's target list
  // with that filter and sort applied; the rows themselves come from
  // `targetBrowser` (with star counts), put in the filtered order.
  const targetFilterPanel = targetList.filterPanel;
  const isTargetFilterInUse = !!targetFilterPanel
    && (targetFilterPanel.selectedCatalog !== CATALOG_ALL
      || targetFilterPanel.selectedCamera !== CAMERA_ALL
      || targetFilterPanel.sort !== SORT_ALPHABETICAL);
  // The "Show" picker's menu: the catalogs, then one entry per camera. One
  // pick sets one of the two shared filters and clears the other. When both
  // are set (from another screen), an extra first entry names the pair.
  const targetShowOptions = useMemo<PopoverSelectOption[]>(() => {
    if (!targetFilterPanel) return [];
    const catalogOptions = targetFilterPanel.catalogOptions.map((catalog) => ({
      id: `${SHOW_CATALOG_PREFIX}${catalog}`,
      label: CATALOG_SHORT_LABELS[catalog] ?? catalog,
      group: 'Catalog',
    }));
    const cameraOptions = targetFilterPanel.cameras.map((camera) => ({
      id: `${SHOW_CAMERA_PREFIX}${camera.name}`,
      label: camera.name,
      detail: camera.targetCount === 0
        ? 'no images yet'
        : `${camera.targetCount} ${camera.targetCount === 1 ? 'target' : 'targets'}`,
      group: 'Imaged with',
    }));
    const isBothSet = targetFilterPanel.selectedCatalog !== CATALOG_ALL && targetFilterPanel.selectedCamera !== CAMERA_ALL;
    const pairOption = isBothSet
      ? [{
        id: SHOW_PAIR_ID,
        label: `${CATALOG_SHORT_LABELS[targetFilterPanel.selectedCatalog] ?? targetFilterPanel.selectedCatalog} · ${targetFilterPanel.selectedCamera}`,
        group: 'Current',
      }]
      : [];
    return [...pairOption, ...catalogOptions, ...cameraOptions];
  }, [targetFilterPanel]);

  const targetShowSelectedId = !targetFilterPanel
    ? null
    : targetFilterPanel.selectedCatalog !== CATALOG_ALL && targetFilterPanel.selectedCamera !== CAMERA_ALL
    ? SHOW_PAIR_ID
    : targetFilterPanel.selectedCamera !== CAMERA_ALL
    ? `${SHOW_CAMERA_PREFIX}${targetFilterPanel.selectedCamera}`
    : `${SHOW_CATALOG_PREFIX}${targetFilterPanel.selectedCatalog}`;

  /**
   * Applies a "Show" pick: a catalog clears the camera filter, a camera clears the catalog filter.
   *
   * @param {string | null} showId - The picked entry's id.
   */
  const handleTargetShowSelect = useCallback((showId: string | null) => {
    if (!targetFilterPanel || !showId || showId === SHOW_PAIR_ID) return;
    if (showId.startsWith(SHOW_CAMERA_PREFIX)) {
      targetFilterPanel.onCameraChange(showId.slice(SHOW_CAMERA_PREFIX.length));
      targetFilterPanel.onCatalogChange(CATALOG_ALL);
    } else {
      targetFilterPanel.onCatalogChange(showId.slice(SHOW_CATALOG_PREFIX.length));
      targetFilterPanel.onCameraChange(CAMERA_ALL);
    }
  }, [targetFilterPanel]);

  const filteredTargetItems = useMemo(() => {
    const filteredEntries = targetList.items;
    if (!isTargetFilterInUse || !filteredEntries) return targetBrowser.items;
    const browserItemsByName = new Map(targetBrowser.items.map((item) => [normalizeTargetName(item.id), item]));
    const orderedItems: SelectableItem[] = [];
    const added = new Set<string>();
    for (const entry of filteredEntries) {
      const match = browserItemsByName.get(normalizeTargetName(entry.id))
        ?? browserItemsByName.get(normalizeTargetName(entry.label));
      if (match && !added.has(match.id)) {
        added.add(match.id);
        orderedItems.push(match);
      }
    }
    return orderedItems;
  }, [targetBrowser.items, targetList.items, isTargetFilterInUse]);

  // The target list with the "Below" badge added, and, when the filter is on,
  // only the targets that are above the horizon. A target with no coordinates
  // cannot be checked, so it gets no badge and the filter hides it.
  const targetListItems = useMemo(() => {
    if (!horizonCheck) return filteredTargetItems;
    const belowHorizonTooltip = isLiveTime ? 'Below the horizon now' : 'Below the horizon at the planning time';
    const markedItems = filteredTargetItems.map((item) => {
      const key = normalizeTargetName(item.id);
      return horizonCheck.belowHorizon.has(key)
        ? { ...item, isBelowHorizon: true, belowHorizonTooltip }
        : item;
    });
    if (!isAboveHorizonOnly) return markedItems;
    return markedItems.filter((item) => {
      const key = normalizeTargetName(item.id);
      return horizonCheck.checked.has(key) && !horizonCheck.belowHorizon.has(key);
    });
  }, [filteredTargetItems, horizonCheck, isAboveHorizonOnly, isLiveTime]);

  // Auto-select the first target once the list loads, unless one is
  // already scoped (including via a deep link elsewhere in this file).
  // It picks from the list as shown, so with the horizon filter on the
  // first pick is a target that is up. A target already picked stays
  // picked when the filter later hides it.
  useEffect(() => {
    if (!scopedTarget && targetListItems.length > 0) {
      setScopedTarget(targetListItems[0].id);
    }
  }, [scopedTarget, targetListItems]);

  const activeSpectralClass =
    navigationMode === 'spectralClass' && scopedSpectralClass !== ALL_SPECTRAL_CLASSES_VALUE
      ? scopedSpectralClass
      : undefined;

  const starsInScopedTarget = useSpectrumList(
    navigationMode === 'target' ? (scopedTarget || undefined) : undefined,
    reloadKey,
    pendingTarget,
    selectedTargetId,
    setPendingTarget
  );
  const starsInSpectralClass = useStarsBySpectralClassList(activeSpectralClass);
  const scopedStarList = activeSpectralClass ? starsInSpectralClass : starsInScopedTarget;

  // Sync details from Backend for selectedSource visibility status
  const targetTimeIso = isLiveTime ? undefined : currentDate.toISOString();

  // The id of the object whose rise and set times are still being asked for; the card shows a
  // loading bar for just those until they come.
  const [visibilityPendingId, setVisibilityPendingId] = useState<string | null>(null);

  useEffect(() => {
    if (!selectedSource || selectedSource.type === 'alignment') return;
    let active = true;
    const controller = new AbortController();
    setVisibilityPendingId(selectedSource.id);

    const fetchDetails = async () => {
      try {
        const details = await callBackend(
          'planetarium:get_visibility',
          {
            // The position lets the backend place a background catalog star the library does not hold.
            objects: [{ id: selectedSource.id, type: selectedSource.type || 'star', ra: selectedSource.ra, dec: selectedSource.dec }],
            time: targetTimeIso || new Date().toISOString()
          },
          { signal: controller.signal }
        );
        if (active && details && details.length > 0) {
          setSelectedSource(prev => {
            if (!prev || prev.id !== selectedSource.id) return prev;
            return {
              ...prev,
              ra: prev.ra,
              dec: prev.dec,
              altitude: details[0].altitude_deg,
              azimuth: details[0].azimuth_deg,
              hourAngle: details[0].meridian?.hour_angle_hours,
              flipRequired: details[0].meridian?.flip_required,
              timeToFlipSeconds: details[0].meridian?.time_to_flip_seconds,
              riseTime: details[0].rise_utc,
              setTime: details[0].set_utc,
              transitTime: details[0].transit_utc,
              aboveHorizon: details[0].above_horizon
            };
          });
        }
      } catch (error: any) {
        if (error?.name !== 'AbortError') {
          console.error("Failed to fetch detailed visibility status", error);
        }
      } finally {
        if (active) setVisibilityPendingId((pending) => (pending === selectedSource.id ? null : pending));
      }
    };

    fetchDetails();
    // A chosen moment gives the same answer every time, so only live time is asked again.
    const interval = isLiveTime ? setInterval(fetchDetails, 10000) : undefined;
    return () => {
      active = false;
      controller.abort();
      if (interval !== undefined) clearInterval(interval);
    };
  }, [selectedSource?.id, targetTimeIso, isLiveTime]);

  /**
   * Resolves the selected object ID to a PlanetariumSource and updates viewport center.
   *
   * Searches three sources in priority order:
   * 1. Local library targets (libraryTargets from usePlanetariumTargets)
   * 2. Target list entries (targetList.targets from useTargetListLogic)
   * 3. Stars in the target list (targetList.stars)
   *
   * @param {string} id - The object ID to look up.
   * @returns {void}
   */
  const handleSelectObject = useCallback((id: string) => {
    setSelectedTargetId(id);
    setPendingTarget(id);
    setTrackOnSlew(false);
    setSlewRequestId(prev => prev + 1);

    // First try libraryTargets (local catalog targets)
    const matchedTarget = libraryTargets.find(target => target.id === id);
    if (matchedTarget) {
      setViewerCenter({ ra: matchedTarget.ra, dec: matchedTarget.dec });
      setSelectedSource({
        id: matchedTarget.id,
        ra: matchedTarget.ra,
        dec: matchedTarget.dec,
        name: matchedTarget.commonName || matchedTarget.id,
        hasSpectra: false,
        hasPhotometry: false,
        type: 'target'
      });
      return;
    }

    // Next, try targets from targetList.targets
    const listMatchedTarget = targetList.targets.find(target => target.id === id);
    if (listMatchedTarget) {
      setViewerCenter({ ra: listMatchedTarget.ra as number, dec: listMatchedTarget.dec as number });
      setSelectedSource({
        id: listMatchedTarget.id || 'unknown',
        ra: listMatchedTarget.ra as number,
        dec: listMatchedTarget.dec as number,
        name: listMatchedTarget.name || listMatchedTarget.id || 'unknown',
        hasSpectra: false,
        hasPhotometry: false,
        type: 'target'
      });
      return;
    }

    // Finally, try stars from targetList.stars
    const matchedStar = targetList.stars.find(star => star.id === id || star.name === id);
    if (matchedStar) {
      const raNum = typeof matchedStar.ra === 'number' ? matchedStar.ra : parseFloat(matchedStar.ra || '0');
      const decNum = typeof matchedStar.dec === 'number' ? matchedStar.dec : parseFloat(matchedStar.dec || '0');
      const starId = matchedStar.name || matchedStar.id || id;
      setViewerCenter({ ra: raNum, dec: decNum });
      setSelectedSource({
        id: starId,
        ra: raNum,
        dec: decNum,
        name: matchedStar.name || matchedStar.id || id,
        hasSpectra: !!matchedStar.hasSpectra || !!matchedStar.has_spectra,
        hasPhotometry: !!matchedStar.hasPhotometry || !!matchedStar.has_photometry,
        type: 'star'
      });
    }
    // setPendingTarget is a useState setter and therefore stable, so listing it
    // changes nothing at runtime -- matching how the sibling callback below
    // already declares it.
  }, [libraryTargets, targetList.targets, targetList.stars, setSelectedTargetId, setPendingTarget]);

  // Consumes a "Show on sky" request (from a quality statement in another
  // display). The hand-off store keeps a request that arrived while this display
  // was hidden, paused or not yet mounted. The request waits until the target
  // lists have loaded, then centers and selects the target like a pick from the
  // list. The sky already follows the shared planning time, so the target is
  // shown at that time.
  const showOnSkyRequest = usePendingHandoff<{ targetId?: string }>('Planetarium', 'showOnSky');
  const targetListsLoaded = libraryTargets.length > 0 || targetList.targets.length > 0;
  useEffect(() => {
    if (!showOnSkyRequest || !targetListsLoaded) return;
    consumeHandoff(showOnSkyRequest);
    const requestedTargetId = showOnSkyRequest.payload?.targetId;
    if (typeof requestedTargetId !== 'string' || requestedTargetId === '') return;
    handleSelectObject(requestedTargetId);
    setScopedTarget(requestedTargetId);
    // Like a pick from the list: the column shows the target's stars.
    setModeDrilledIn('target', true);
  }, [showOnSkyRequest, targetListsLoaded, handleSelectObject, setModeDrilledIn]);

  /**
   * Centers the sky map on and selects a star handed off from Astronomy
   * Manager's "Locate in Planetarium" action.
   *
   * Unlike handleSelectObject, this doesn't look the star up in
   * libraryTargets/targetList -- those lists are paginated/query-scoped and
   * may not contain the handed-off star, so the RA/Dec and flags carried in
   * the hand-off itself are used directly instead.
   *
   * Also zooms the map to `STAR_PICK_FOV_DEG` and turns tracking on.
   *
   * @param {PlanetariumSource} source - The star to center on and select.
   * @returns {void}
   */
  const applyLocateStar = useCallback((source: PlanetariumSource) => {
    setViewerCenter({ ra: source.ra, dec: source.dec });
    setSelectedSource(source);
    setSelectedTargetId(source.id);
    setPendingTarget(source.id);
    setCurrentFOV(STAR_PICK_FOV_DEG);
    setTrackOnSlew(true);
    setSlewRequestId(prev => prev + 1);
  }, [setSelectedTargetId, setPendingTarget]);

  /**
   * Selects a target from the target list (level 1): scopes the star list
   * to it, centers and selects it on the map, and slides the column to the
   * target's star list (level 2).
   *
   * @param {string} id - The target's id.
   * @returns {void}
   */
  const handleSelectScopedTarget = useCallback((id: string) => {
    setScopedTarget(id);
    // A pick made by moving through the list with the arrow keys stays on
    // level 1, so the list can still be browsed; a click (or Enter) opens the stars.
    if (isArrowKeyPickRef.current) {
      handleSelectObject(id);
      return;
    }
    if (pendingFocusRef.current === null) pendingFocusRef.current = 'levelTwo';
    // The slide starts at once; centering the sky on the target waits until
    // it ends, so the two do not share the main thread and the slide stays smooth.
    setModeDrilledIn('target', true);
    runAfterSlide(() => handleSelectObject(id));
  }, [handleSelectObject, setModeDrilledIn, runAfterSlide]);

  /**
   * Starts loading a target's first page of stars while the pointer rests on
   * (or the keyboard focus reaches) its row, so the star list is ready when
   * the column slides to it.
   *
   * @param {React.SyntheticEvent<HTMLDivElement>} event - A pointer-over or focus event inside level 1.
   */
  const queryClient = React.useContext(QueryClientContext);
  const lastPrefetchedTargetRef = React.useRef<string>('');
  const handleLevelOnePrefetch = useCallback((event: React.SyntheticEvent<HTMLDivElement>) => {
    if (navigationMode !== 'target' || !queryClient || !(event.target instanceof Element)) return;
    const row = event.target.closest('label');
    const targetId = row?.querySelector<HTMLInputElement>('input[type="radio"]')?.value;
    if (!targetId || targetId === lastPrefetchedTargetRef.current) return;
    lastPrefetchedTargetRef.current = targetId;
    void prefetchAstronomyList(queryClient, { targetId, search: '', limit: STAR_LIST_PAGE_SIZE, offset: 0 });
  }, [navigationMode, queryClient]);

  /**
   * Selects a star from either secondary star list (a target's stars, or
   * a spectral class's stars). Both lists' items already carry their own
   * ra/dec, so the star is centered/selected directly via applyLocateStar
   * instead of handleSelectObject's lookup chain, which only searches
   * libraryTargets and the viewport-scoped targetList -- neither of which
   * is guaranteed to contain a star found this way.
   *
   * @param {string} id - The star's id, as `scopedStarList.items` uses it.
   * @returns {void}
   */
  const handleSelectScopedStar = useCallback((id: string) => {
    const item = scopedStarList.items.find((listItem) => listItem.value === id);
    if (!item || typeof item.ra !== 'number' || typeof item.dec !== 'number') return;
    applyLocateStar({
      id: item.value,
      ra: item.ra,
      dec: item.dec,
      name: item.label,
      // The card shows the same magnitude and spectral type as the list row.
      magnitude: item.magnitude,
      hasCatalogMagnitude: item.hasCatalogMagnitude,
      spectralType: item.spectralType,
      hasSpectra: !!item.hasSpectra,
      hasPhotometry: !!item.hasPhotometry,
      type: 'star',
    });
  }, [scopedStarList.items, applyLocateStar]);

  // Consumes a star handed off from Astronomy Manager's "Locate in
  // Planetarium" action. The request waits in the hand-off store, so the same
  // hand-off works whether this display is running, hidden, paused or is
  // switched to as part of the hand-off.
  // The hand-off carries only the id, name, position and data flags, so the
  // star's library record is read to give its card the same name, magnitude
  // and spectral type as a pick from the star list.
  // When the hand-off names the star's target (the Astronomy Manager sends the
  // target picked in its list), the column opens that target's star list
  // (level 2, By target) with the star picked in it.
  useHandoffConsumer<PlanetariumSource & { targetId?: string }>('Planetarium', 'locateStar', (handoff) => {
    if (!handoff) return;
    const { targetId: handoffTargetId, ...source } = handoff;
    if (typeof handoffTargetId === 'string' && handoffTargetId !== '') {
      setNavigationMode('target');
      setScopedTarget(handoffTargetId);
      setModeDrilledIn('target', true);
    }
    applyLocateStar(source);
    void completeStarFromLibrary(source).then((complete) => {
      if (complete === source) return;
      setSelectedSource((current) => (current && current.id === source.id ? { ...current, ...complete } : current));
    });
  });

  /**
   * Handles source selection from the canvas.
   *
   * @param {PlanetariumSource | null} source - The selected source, or null to deselect.
   * @returns {void}
   */
  const handleSelectSource = useCallback((source: PlanetariumSource | null) => {
    setSelectedSource(source);
    if (source) {
      setSelectedTargetId(source.id);
      setPendingTarget(source.id);
      if (source.type === 'alignment' && source.alignmentAttempt?.timestamp) {
        // source.alignmentAttempt.timestamp is in unix epoch seconds. Showing the
        // sky at that moment sets the shared planning time.
        setPlanningTime(new Date(source.alignmentAttempt.timestamp * 1000));
      }
    } else {
      setSelectedTargetId('');
      setPendingTarget('');
    }
  }, [setSelectedTargetId, setPendingTarget, setPlanningTime]);

  /**
   * Throttled callback invoked by CelestialSkyMap when the viewport center moves.
   * Updates the queried star region only when the viewport has moved more than
   * 15% of the current FOV to prevent redundant network requests during panning.
   *
   * @param {number} ra - New center Right Ascension in degrees.
   * @param {number} dec - New center Declination in degrees.
   * @returns {void}
   */
  const lastQueryCenter = React.useRef<{ ra: number; dec: number } | null>(null);

  const handleCenterChange = useCallback((ra: number, dec: number) => {
    if (!lastQueryCenter.current) {
      lastQueryCenter.current = { ra, dec };
      setViewerCenter({ ra, dec });
      return;
    }
    const dra = ra - lastQueryCenter.current.ra;
    const ddec = dec - lastQueryCenter.current.dec;
    const dist = Math.sqrt(dra * dra + ddec * ddec);

    // If movement exceeds 15% of the current FOV, update queried region center
    if (dist > currentFOV * 0.15) {
      lastQueryCenter.current = { ra, dec };
      setViewerCenter({ ra, dec });
    }
  }, [currentFOV]);

  // The center of the framing grid: the picked object, else the chosen target.
  const framingCenter = useMemo(() => {
    if (selectedSource) return { ra: selectedSource.ra, dec: selectedSource.dec };
    if (selectedTarget) return { ra: selectedTarget.ra, dec: selectedTarget.dec };
    return null;
  }, [selectedSource, selectedTarget]);

  // In the spectral class mode the map shows only the stars of the left list:
  // every classed star with "All", or the chosen class's stars, each in its
  // class color. The background catalogs, other library stars and the target
  // markers are hidden; constellation lines, the grid, the horizon and the
  // telescope and field-of-view outlines stay. By target shows everything.
  const isSpectralClassMode = navigationMode === 'spectralClass';
  const classStars = useClassStarSources(isSpectralClassMode, activeSpectralClass);
  const visibleStars = isSpectralClassMode ? classStars.sources : stars;
  // The sky follows the target list's filters: the "Show" pick and "Above
  // horizon only". The search text and the sort order do not change the sky,
  // so typing never makes it flicker. One set of visible targets serves both
  // the target circles and the Images layer. The selected target stays drawn.
  const isShowFilterOn = !!targetFilterPanel
    && (targetFilterPanel.selectedCatalog !== CATALOG_ALL || targetFilterPanel.selectedCamera !== CAMERA_ALL);
  const isHorizonFilterOn = isAboveHorizonOnly && horizonCheck !== null;
  const libraryTargetKeys = useMemo(
    () => new Set(libraryTargets.map((target) => normalizeTargetName(target.id))),
    [libraryTargets],
  );
  const skyVisibleTargetKeys = useMemo(() => buildSkyVisibleTargetKeys(
    libraryTargets.map((target) => target.id),
    isShowFilterOn && targetList.items ? targetList.items.flatMap((item) => [item.id, item.label]) : null,
    isHorizonFilterOn ? horizonCheck : null,
  ), [libraryTargets, isShowFilterOn, targetList.items, isHorizonFilterOn, horizonCheck]);
  const alwaysShownTargetIds = useMemo(
    () => [scopedTarget, selectedTargetId ?? ''],
    [scopedTarget, selectedTargetId],
  );

  const visibleTargets = useMemo(() => {
    if (isSpectralClassMode) return [];
    if (skyVisibleTargetKeys === null) return targets;
    return targets.filter((target) => isSkyTargetShown(target.id, skyVisibleTargetKeys, libraryTargetKeys, alwaysShownTargetIds));
  }, [isSpectralClassMode, targets, skyVisibleTargetKeys, libraryTargetKeys, alwaysShownTargetIds]);

  // The Images layer draws only the pictures of the targets the sky shows; a
  // hidden target's picture is never loaded, because pictures load when drawn.
  const visibleStackPictures = useMemo(
    () => filterSkyPictures(stackPictureLayer.pictures, skyVisibleTargetKeys, libraryTargetKeys, alwaysShownTargetIds),
    [stackPictureLayer.pictures, skyVisibleTargetKeys, libraryTargetKeys, alwaysShownTargetIds],
  );
  const stackPictures = useMemo(() => ({
    show: stackPictureLayer.showImages,
    opacity: stackPictureLayer.opacity,
    pictures: visibleStackPictures,
    getDrawable: stackPictureLayer.getDrawable,
  }), [stackPictureLayer.showImages, stackPictureLayer.opacity, visibleStackPictures, stackPictureLayer.getDrawable]);

  /**
   * Slides back to level 1. The picked target (or class) stays highlighted.
   *
   * @param {boolean} fromKeyboard - True when a key started it; the focus then goes to the picked row.
   */
  const backToLevelOne = useCallback((fromKeyboard: boolean) => {
    pendingFocusRef.current = fromKeyboard ? 'list' : null;
    setModeDrilledIn(navigationMode, false);
  }, [navigationMode, setModeDrilledIn]);

  /**
   * The Back button. A click made with Enter or Space has `detail` 0.
   *
   * @param {React.MouseEvent<HTMLButtonElement>} event - The click.
   */
  const handleBackToLevelOne = useCallback((event: React.MouseEvent<HTMLButtonElement>) => {
    backToLevelOne(event.detail === 0);
  }, [backToLevelOne]);

  /**
   * Escape anywhere on level 2 slides back to level 1.
   *
   * @param {React.KeyboardEvent<HTMLDivElement>} event - The key press.
   */
  const handleLevelTwoKeyDown = useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Escape' || event.defaultPrevented) return;
    event.preventDefault();
    backToLevelOne(true);
  }, [backToLevelOne]);

  /**
   * Keyboard use on level 1. Arrow keys move through the list and pick each
   * row (the sky map follows), but do not slide to level 2, so the list can be
   * browsed. Enter opens the picked row's stars.
   *
   * @param {React.KeyboardEvent<HTMLDivElement>} event - The key press.
   */
  const handleLevelOneKeyDown = useCallback((event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key.startsWith('Arrow')) {
      // The browser picks the next radio button during this key press; the
      // flag tells the pick handlers it came from an arrow key.
      isArrowKeyPickRef.current = true;
      window.setTimeout(() => { isArrowKeyPickRef.current = false; }, 0);
      return;
    }
    const isOnRadio = event.target instanceof HTMLInputElement && event.target.type === 'radio';
    if (event.key === 'Enter' && isOnRadio) {
      event.preventDefault();
      pendingFocusRef.current = 'back';
      setModeDrilledIn(navigationMode, true);
    }
  }, [navigationMode, setModeDrilledIn]);

  /**
   * Picks a spectral class and, unless it came from an arrow key, opens its stars.
   *
   * @param {string} classId - The class id, such as "K" or the "All" entry.
   */
  const handleSelectScopedSpectralClass = useCallback((classId: string) => {
    setScopedSpectralClass(classId);
    if (isArrowKeyPickRef.current) return;
    if (pendingFocusRef.current === null) pendingFocusRef.current = 'levelTwo';
    setModeDrilledIn('spectralClass', true);
  }, [setModeDrilledIn]);

  // The whole left column folds to a thin strip, like the Astronomy Manager's; the choice is remembered for this viewer.
  const [browserCollapsed, setBrowserCollapsed] = useCollapsibleColumn('astrometrics:planetarium:targetListCollapsed');
  const browserStripTitle = navigationMode === 'target' ? 'Targets' : 'Spectral classes';
  const browserSelectedLabel = navigationMode === 'target'
    ? targetBrowser.items.find((item) => item.id === scopedTarget)?.label
    : spectralClassBrowser.items.find((item) => item.id === scopedSpectralClass)?.id;
  // The star picked in the star list, shown on the strip after the target or class.
  const browserSelectedStar = scopedStarList.items.find((item) => item.value === selectedTargetId)?.label;

  // Says why the target list is empty: nothing loaded, the shared filter, or the horizon box.
  const targetListEmptyMessage = targetBrowser.items.length === 0
    ? 'No targets found.'
    : isAboveHorizonOnly && horizonCheck && filteredTargetItems.length > 0
    ? 'No targets are above the horizon.'
    : 'No targets match the filter.';

  // The row under the target search: a "Show" picker (a catalog, or the
  // targets one camera imaged) and a "Sort" picker. Both read and set the
  // shared target filter (`targetList.filterPanel`), the same choices Image
  // Processing's Filter Targets panel shows; only the look differs.
  const targetFilterRow = targetFilterPanel ? (
    <>
      <PopoverSelect
        label="Show"
        size="compact"
        options={targetShowOptions}
        selectedId={targetShowSelectedId}
        onSelect={handleTargetShowSelect}
      />
      <PopoverSelect
        label="Sort"
        size="compact"
        options={TARGET_SORT_OPTIONS}
        selectedId={targetFilterPanel.sort}
        onSelect={(sortId) => { if (sortId) targetFilterPanel.onSortChange(sortId as SortChoice); }}
      />
    </>
  ) : undefined;

  // The two levels of each browse mode: level 1 is the target (or class)
  // list; level 2 is the star list of the picked target (or class).
  const levelOneTitle = navigationMode === 'target' ? 'Targets' : 'Spectral classes';
  const levelTwoTitle = navigationMode === 'target'
    ? (targetBrowser.items.find((item) => item.id === scopedTarget)?.label ?? (scopedTarget || 'Stars'))
    : activeSpectralClass
    ? `Class ${activeSpectralClass} stars`
    : 'All stars';

  const levelOneList = navigationMode === 'target' ? (
    <RadioListManager
      title="Targets"
      className="planetarium-display__level-list"
      onCollapsedChange={setBrowserCollapsed}
      hideLabel="Hide target list"
      items={targetListItems}
      selectedId={scopedTarget}
      pendingId={scopedTarget}
      onSelect={handleSelectScopedTarget}
      filterText={targetBrowser.filterText}
      onFilterOptionChange={() => {}}
      onFilterTextChange={targetBrowser.setFilterText}
      filterPlaceholder="Search targets..."
      filterRow={targetFilterRow}
      headerContent={
        <span className="planetarium-display__horizon-filter">
          <Checkbox
            size="compact"
            label="Above horizon only"
            checked={isAboveHorizonOnly}
            onChange={handleAboveHorizonOnlyChange}
            disabled={!horizonCheck}
            title={isLiveTime
              ? 'Show only the targets that are above the horizon now'
              : 'Show only the targets that are above the horizon at the planning time'}
          />
        </span>
      }
      highlightedIds={targetList.highlightedIds}
      isLoading={targetBrowser.isLoading}
      loadingMessage="Loading targets"
      emptyMessage={targetListEmptyMessage}
      isError={targetBrowser.isError}
      errorMessage="Could not load the targets."
      onRetry={targetBrowser.retry}
    />
  ) : (
    <RadioListManager
      title="Spectral classes"
      className="planetarium-display__level-list"
      onCollapsedChange={setBrowserCollapsed}
      hideLabel="Hide class list"
      items={spectralClassBrowser.items}
      selectedId={scopedSpectralClass}
      pendingId={scopedSpectralClass}
      onSelect={handleSelectScopedSpectralClass}
      filterText={spectralClassBrowser.filterText}
      onFilterOptionChange={() => {}}
      onFilterTextChange={spectralClassBrowser.setFilterText}
      filterPlaceholder="Search classes..."
      isLoading={spectralClassBrowser.isLoading}
      loadingMessage="Scanning the catalog for spectral classes…"
      emptyMessage="No spectral classes found."
      isError={spectralClassBrowser.isError}
      errorMessage="Could not load the spectral classes."
      onRetry={spectralClassBrowser.retry}
    />
  );

  const levelTwoList = (
    <RadioListManager
      title={levelTwoTitle}
      className="planetarium-display__level-list"
      onCollapsedChange={setBrowserCollapsed}
      hideLabel="Hide star list"
      headerStart={
        <Button
          ref={backButtonRef}
          variant="ghost"
          size="small"
          onClick={handleBackToLevelOne}
          title={`Back to the ${levelOneTitle.toLowerCase()} (Escape)`}
          aria-label={`Back to ${levelOneTitle}`}
        >
          ‹ {navigationMode === 'target' ? 'Targets' : 'Classes'}
        </Button>
      }
      items={scopedStarList.items}
      selectedId={selectedTargetId}
      pendingId={pendingTarget}
      onSelect={handleSelectScopedStar}
      onFilterOptionChange={() => {}}
      filterText={scopedStarList.filterText}
      onFilterTextChange={scopedStarList.setFilterText}
      filterPlaceholder="Search stars..."
      legend={
        <>
          <span><span className="selectable-list__badge selectable-list__badge--spectra">S</span> spectrum</span>
          <span><span className="selectable-list__badge selectable-list__badge--photometry">P</span> photometry</span>
        </>
      }
      isLoading={scopedStarList.isLoading}
      loadingMessage="Loading stars"
      emptyMessage="No stars found for this selection."
      isError={scopedStarList.isError}
      errorMessage="Could not load the stars."
      onRetry={scopedStarList.retry}
      page={activeSpectralClass ? undefined : starsInScopedTarget.page}
      onPageChange={activeSpectralClass ? undefined : starsInScopedTarget.setPage}
      hasMore={activeSpectralClass ? false : starsInScopedTarget.hasMore}
      totalPages={activeSpectralClass ? undefined : starsInScopedTarget.totalPages}
    />
  );

  const leftPanel = (
    <CollapsibleColumn
      collapsed={browserCollapsed}
      onCollapsedChange={setBrowserCollapsed}
      stripTitle={browserStripTitle}
      selectedLabel={browserSelectedLabel}
      selectedDetail={browserSelectedStar}
      showLabel={navigationMode === 'target' ? 'Show target list' : 'Show class list'}
    >
      <div className="astronomy-display__browser">
        <div className="astronomy-display__mode-toggle">
          <Tabs
            idPrefix="navigation-mode"
            ariaLabel="Browse by"
            selectedId={navigationMode}
            onChange={(modeId) => setNavigationMode(modeId as typeof navigationMode)}
            tabs={[
              { id: 'target', label: 'By target' },
              { id: 'spectralClass', label: 'By spectral class' },
            ]}
          />
        </div>

        {/* The two levels are stacked in one clipped box; showing level 2
            slides level 1 out to the left and level 2 in from the right. The
            level that is off screen is inert, so Tab and screen readers skip it. */}
        <div className="planetarium-display__drill">
          <div
            className={`planetarium-display__drill-track${isDrilledIn ? ' planetarium-display__drill-track--level-two' : ''}`}
            data-testid="planetarium-drill-track"
            data-level={isDrilledIn ? 'stars' : 'list'}
          >
            <div
              ref={levelOnePaneRef}
              className="planetarium-display__drill-pane planetarium-display__drill-pane--level-one"
              inert={isDrilledIn}
              onKeyDownCapture={handleLevelOneKeyDown}
              onPointerOver={handleLevelOnePrefetch}
              onFocus={handleLevelOnePrefetch}
            >
              {levelOneList}
            </div>
            <div
              ref={levelTwoPaneRef}
              tabIndex={-1}
              aria-label={`Stars of ${levelTwoTitle}`}
              className="planetarium-display__drill-pane planetarium-display__drill-pane--level-two"
              inert={!isDrilledIn}
              onKeyDown={handleLevelTwoKeyDown}
            >
              {levelTwoList}
            </div>
          </div>
        </div>
      </div>
    </CollapsibleColumn>
  );

  const centerPanel = (
    <div className="planetarium-viewer-container" ref={viewerContainerRef}>
      <PlanetariumToolbar
        showStars={showStars}
        onToggleStars={setShowStars}
        showFOV={showFOVOutline}
        onToggleFOV={setShowFOVOutline}
        showEnvironment={showEnvironment}
        onToggleEnvironment={setShowEnvironment}
        showGrid={showGrid}
        onToggleGrid={setShowGrid}
        showCatalog={showCatalog}
        onToggleCatalog={setShowCatalog}
        showConstellations={showConstellations}
        onToggleConstellations={setShowConstellations}
        showTelescope={showTelescope}
        onToggleTelescope={setShowTelescope}
        showTrackingRisk={showTrackingRisk}
        onToggleTrackingRisk={setShowTrackingRisk}
        availableSessions={availableSessions}
        selectedSessionId={selectedSessionId}
        onSelectSession={setSelectedSessionId}
        currentFOV={currentFOV}
        onOpenTimeModal={() => setIsTimeModalOpen(true)}
        showImages={stackPictureLayer.showImages}
        onToggleImages={stackPictureLayer.setShowImages}
        imagesOpacity={stackPictureLayer.opacity}
        onImagesOpacityChange={stackPictureLayer.setOpacity}
      />

      <CelestialSkyMap
        center={selectedSource ? { ra: selectedSource.ra, dec: selectedSource.dec } : (selectedTarget ? { ra: selectedTarget.ra, dec: selectedTarget.dec } : null)}
        sources={visibleStars}
        targets={visibleTargets}
        location={location}
        showStars={showStars}
        showFOV={showFOVOutline}
        showEnvironment={showEnvironment}
        showGrid={showGrid}
        showCatalog={showCatalog}
        showConstellations={showConstellations}
        constellationLines={constellationLines}
        showTelescope={showTelescope}
        showTrackingRisk={showTrackingRisk}
        alignmentTargets={activeAlignmentTargets}
        trackingRisk={trackingRisk}
        polarAlignment={activePolarAlignment}
        selectedSessionId={selectedSessionId}
        simulationDate={isLiveTime ? undefined : currentDate}
        fov={currentFOV}
        onFOVChange={setCurrentFOV}
        onSelectSource={handleSelectSource}
        onRightClickSource={(src, pos) => {
          setContextSource(src);
          setContextPos(pos);
        }}
        selectedTargetId={selectedTargetId}
        slewRequestId={slewRequestId}
        trackOnSlew={trackOnSlew}
        onCenterChange={handleCenterChange}
        sensorFovWidthDeg={equipmentConfig?.fovWidthDeg}
        sensorFovHeightDeg={equipmentConfig?.fovHeightDeg}
        framingPanels={framingPanels}
        stackPictures={stackPictures}
        stackPicturesVersion={stackPictureLayer.loadedVersion}
      />

      <DeepCatalogPrompt status={deepCatalogStatus} />

      {isSpectralClassMode && (
        <StarColorLegend
          shownCount={classStars.sources.length}
          spectralClass={activeSpectralClass}
          isLoading={classStars.isLoading}
          isError={classStars.isError}
          onRetry={classStars.retry}
        />
      )}

      <PlanetariumDateTimeModal
        isOpen={isTimeModalOpen}
        onClose={() => setIsTimeModalOpen(false)}
        currentDate={dialogDate}
        onDateChange={handleDateChange}
        onResetToNow={handleResetToNow}
      />

      {/* Selected Source Details Card (Star/Target vs Alignment Point) */}
      {selectedSource && selectedSource.type === 'alignment' && (
        <AlignmentPointCard
          source={selectedSource}
          observerLocation={location}
          onClose={() => handleSelectSource(null)}
        />
      )}

      {/*
        The selected object's card and its framing panel share one column on
        the left of the sky. The framing panel extends down from the card's
        bottom edge as one joined unit, so the two never cover each other;
        the column scrolls as a whole when the window is too short.
      */}
      <div
        ref={sideStackRef}
        style={sideStackTop !== null ? { top: sideStackTop } : undefined}
        className="planetarium-side-stack"
        data-testid="planetarium-side-stack"
      >
        {selectedSource && selectedSource.type !== 'alignment' && (
          <PlanetariumInfoCard
            source={selectedSource}
            observerLocation={location}
            onClose={() => handleSelectSource(null)}
            isLibraryTarget={libraryTargets.some((target) => target.id === selectedSource.id)}
            onToggleFraming={() => setIsFramingOpen((open) => !open)}
            isFramingOpen={isFramingOpen}
            framingButtonRef={framingButtonRef}
            atTime={currentDate}
            visibilityPending={visibilityPendingId === selectedSource.id}
          />
        )}
        <FramingPanel
          targetName={selectedSource?.name ?? selectedTarget?.commonName ?? selectedTarget?.id ?? null}
          center={framingCenter}
          onFramingPanelsChange={setFramingPanels}
          isOpen={isFramingOpen}
          onClose={closeFraming}
        />
      </div>

      {contextSource && contextPos && (
        <PlanetariumContextMenu
          source={contextSource}
          x={contextPos.x}
          y={contextPos.y}
          telescopeConnected={!!telescopeConnection}
          onClose={() => {
            setContextSource(null);
            setContextPos(null);
          }}
        />
      )}
    </div>
  );

  return (
    <GenericDisplayLayout
      className="planetarium-display"
      leftPanel={leftPanel}
      centerPanel={centerPanel}
    />
  );
};
