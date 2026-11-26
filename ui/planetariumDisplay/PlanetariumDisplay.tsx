/**
 * @module planetariumDisplay
 * @fileoverview Root container component for the Planetarium display panel.
 *
 * Coordinates the CelestialSkyMap canvas renderer, the left browser column
 * (`PlanetariumBrowserColumn`), the floating PlanetariumInfoCard, and the
 * framing panel. Keeps the selected object, takes the hand-offs from other
 * displays, and runs the three-priority source lookup used by
 * handleSelectObject. The left column's filters, its two levels, the selected
 * object's visibility and the objects the sky draws live in their own hooks.
 */

import React, { useState, useCallback, useMemo, useEffect } from 'react';
import { GenericDisplayLayout } from '../common/components/GenericDisplayLayout';
import { CelestialSkyMap } from './components/CelestialSkyMap';
import { PlanetariumInfoCard } from './components/PlanetariumInfoCard';
import { AlignmentPointCard } from './components/AlignmentPointCard';
import { PlanetariumToolbar } from './components/PlanetariumToolbar';
import { PlanetariumContextMenu } from './components/PlanetariumContextMenu';
import { PlanetariumDateTimeModal } from './components/PlanetariumDateTimeModal';
import { PlanetariumBrowserColumn } from './components/PlanetariumBrowserColumn';
import { FramingPanel } from './components/FramingPanel';
import type { FramingPanelCorners } from './layers/FramingOverlay';
import { usePlanningTime } from '../common/context/PlanningTimeContext';
import { useConstellationLines } from './hooks/useConstellationLines';
import { useObserverLocation } from './hooks/useObserverLocation';
import { useTargetsBelowHorizon } from './hooks/useTargetsBelowHorizon';
import { useOverlayToggles } from './hooks/useOverlayToggles';
import { useEquipmentConfiguration } from '../common/hooks/useEquipmentConfiguration';
import { useAlignmentSessionData } from './hooks/useAlignmentSessionData';
import { useTrackingRiskMap } from './hooks/useTrackingRiskMap';
import { useSourceMerging } from './hooks/useSourceMerging';
import { useTargetListFilter } from './hooks/useTargetListFilter';
import { useDrillInColumn, type BrowseMode } from './hooks/useDrillInColumn';
import { useSelectedSourceVisibility } from './hooks/useSelectedSourceVisibility';
import { useSkyVisibleObjects } from './hooks/useSkyVisibleObjects';
import { PlanetariumSource } from '../common/types/planetariumTypes';
import { useTargetContext } from '../common/context/TargetContext';
import { useTargetListLogic } from '../common/hooks/useTargetListLogic';
import { useRemoteStatusContext } from '../common/context/RemoteStatusContext';
import { useTelescopeStatus } from '../common/hooks/useTelescopeStatus';
import { safeParse } from './utils/coordinateUtils';
import { DeepCatalogPrompt } from './components/DeepCatalogPrompt';
import { StarColorLegend } from './components/StarColorLegend';
import { useSideStackTop } from './hooks/useSideStackTop';
import { useStackPictures } from './hooks/useStackPictures';
import { completeStarFromLibrary } from './utils/libraryStarCard';
import { consumeHandoff, usePendingHandoff, useHandoffConsumer } from '../common/utils/displayHandoffStore';
import { useTargetBrowserItems } from '../astronomyManager/hooks/useTargetBrowserItems';
import { useSpectralClassBrowserItems, ALL_SPECTRAL_CLASSES_VALUE } from '../astronomyManager/hooks/useSpectralClassBrowserItems';
import { useSpectrumList } from '../astronomyManager/hooks/useSpectrumList';
import { useStarsBySpectralClassList } from '../astronomyManager/hooks/useStarsBySpectralClassList';
import { useReportModeReady } from '../common/utils/appBootReadiness';
import './styles/planetariumDisplay.css';

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
  const [navigationMode, setNavigationMode] = useState<BrowseMode>('target');
  const [scopedTarget, setScopedTarget] = useState<string>('');
  const [scopedSpectralClass, setScopedSpectralClass] = useState<string>(ALL_SPECTRAL_CLASSES_VALUE);

  // Drill-in: each browse mode shows either its list (level 1) or the stars
  // of the picked entry (level 2).
  const drill = useDrillInColumn(navigationMode);
  const { setModeDrilledIn, openLevelTwo, isArrowKeyPick, runAfterSlide } = drill;


  const targetBrowser = useTargetBrowserItems();
  const spectralClassBrowser = useSpectralClassBrowserItems();
  // The sky map's own catalog sources (useSourceMerging) don't expose a
  // loading flag; the target/star browser sidebar's does and fetches on the
  // same mount, so it's a reasonable proxy for "this view has real data."
  useReportModeReady('Planetarium', !targetBrowser.isLoading);

  // Which targets are under the horizon at the planning time (null until the
  // site is known). Drives the muted "Below" badge and the horizon filter.
  const horizonCheck = useTargetsBelowHorizon(libraryTargets, location, !isLocationLoading, planningTime);

  // The shared target filter (By catalog, By camera, A–Z or Newest), the same
  // one Image Processing shows in its Filter Targets panel, and the horizon box.
  const targetFilter = useTargetListFilter({
    filterPanel: targetList.filterPanel,
    filteredEntries: targetList.items,
    browserItems: targetBrowser.items,
    horizonCheck,
    isLiveTime,
  });
  const { targetListItems } = targetFilter;

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
  const visibilityPendingId = useSelectedSourceVisibility(selectedSource, setSelectedSource, targetTimeIso, isLiveTime);


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
    if (isArrowKeyPick()) {
      handleSelectObject(id);
      return;
    }
    // The slide starts at once; centering the sky on the target waits until
    // it ends, so the two do not share the main thread and the slide stays smooth.
    openLevelTwo('target');
    runAfterSlide(() => handleSelectObject(id));
  }, [handleSelectObject, isArrowKeyPick, openLevelTwo, runAfterSlide]);


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


  const isSpectralClassMode = navigationMode === 'spectralClass';
  const alwaysShownTargetIds = useMemo(
    () => [scopedTarget, selectedTargetId ?? ''],
    [scopedTarget, selectedTargetId],
  );
  const { classStars, visibleStars, visibleTargets, stackPictures } = useSkyVisibleObjects({
    isSpectralClassMode,
    activeSpectralClass,
    stars,
    targets,
    libraryTargets,
    isShowFilterOn: targetFilter.isShowFilterOn,
    filteredEntries: targetList.items,
    isAboveHorizonOnly: targetFilter.isAboveHorizonOnly,
    horizonCheck,
    alwaysShownTargetIds,
    stackPictureLayer,
  });

  /**
   * Picks a spectral class and, unless it came from an arrow key, opens its stars.
   *
   * @param {string} classId - The class id, such as "K" or the "All" entry.
   */
  const handleSelectScopedSpectralClass = useCallback((classId: string) => {
    setScopedSpectralClass(classId);
    if (isArrowKeyPick()) return;
    openLevelTwo('spectralClass');
  }, [isArrowKeyPick, openLevelTwo]);

  const leftPanel = (
    <PlanetariumBrowserColumn
      navigationMode={navigationMode}
      onNavigationModeChange={setNavigationMode}
      drill={drill}
      targetBrowser={targetBrowser}
      targetFilter={targetFilter}
      filterPanel={targetList.filterPanel}
      canFilterByHorizon={!!horizonCheck}
      isLiveTime={isLiveTime}
      highlightedIds={targetList.highlightedIds}
      scopedTarget={scopedTarget}
      onSelectTarget={handleSelectScopedTarget}
      spectralClassBrowser={spectralClassBrowser}
      scopedSpectralClass={scopedSpectralClass}
      activeSpectralClass={activeSpectralClass}
      onSelectSpectralClass={handleSelectScopedSpectralClass}
      starList={scopedStarList}
      starListPaging={starsInScopedTarget}
      selectedId={selectedTargetId}
      pendingId={pendingTarget}
      onSelectStar={handleSelectScopedStar}
    />
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
