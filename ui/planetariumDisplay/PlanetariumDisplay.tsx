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
import { useConstellationLines } from './hooks/useConstellationLines';
import { useObserverLocation } from './hooks/useObserverLocation';
import { useOverlayToggles } from './hooks/useOverlayToggles';
import { useEquipmentConfiguration } from './hooks/useEquipmentConfiguration';
import { useAlignmentSessionData } from './hooks/useAlignmentSessionData';
import { useSourceMerging } from './hooks/useSourceMerging';
import { EquipmentConfigPanel } from './components/EquipmentConfigPanel';
import { PlanetariumSource } from '../common/types/planetariumTypes';
import { callBackend } from '../common/services/backendApi';
import { useTargetContext } from '../common/context/TargetContext';
import { useTargetListLogic } from '../common/hooks/useTargetListLogic';
import { useRemoteStatusContext } from '../common/context/RemoteStatusContext';
import { useTelescopeStatus } from '../common/hooks/useTelescopeStatus';
import { safeParse } from './utils/coordinateUtils';
import { DeepCatalogPrompt } from './components/DeepCatalogPrompt';
import { useNavigationTarget, NavigationIntent } from '../common/utils/displayCoordinator';
import { useTargetBrowserItems } from '../astronomyManager/hooks/useTargetBrowserItems';
import { useSpectralClassBrowserItems, ALL_SPECTRAL_CLASSES_VALUE } from '../astronomyManager/hooks/useSpectralClassBrowserItems';
import { useSpectrumList } from '../astronomyManager/hooks/useSpectrumList';
import { useStarsBySpectralClassList } from '../astronomyManager/hooks/useStarsBySpectralClassList';
import { spectralClassLetter } from '../astronomyManager/utils/starDisplayFormat';
import '../common/styles/segmentedToggle.css';
import './styles/planetariumDisplay.css';

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
    showAlignment, setShowAlignment,
  } = useOverlayToggles();

  // Mount tracking risk heatmap toggle state
  const [showTrackingRisk, setShowTrackingRisk] = useState<boolean>(false);

  // Historical session review, cumulative tracking data, and telescope log sync
  const {
    availableSessions,
    selectedSessionId,
    setSelectedSessionId,
    isSyncingLogs,
    handleSyncLogs,
    activeAlignmentAttempts,
    activeCumulativeTrackingAttempts,
    activePolarAlignment,
  } = useAlignmentSessionData(telemetry);

  const [viewerCenter, setViewerCenter] = useState<{ ra: number; dec: number } | null>(null);
  const [currentFOV, setCurrentFOV] = useState<number>(90.0);

  // Date and Time controls
  const [currentDate, setCurrentDate] = useState<Date>(new Date());
  const [isTimeModalOpen, setIsTimeModalOpen] = useState<boolean>(false);
  // When true, currentDate ticks forward with the system clock every second
  const [isLiveTime, setIsLiveTime] = useState<boolean>(true);

  useEffect(() => {
    if (!isLiveTime) return;
    const interval = setInterval(() => setCurrentDate(new Date()), 1000);
    return () => clearInterval(interval);
  }, [isLiveTime]);

  const handleDateChange = useCallback((date: Date) => {
    setIsLiveTime(false);
    setCurrentDate(date);
  }, []);

  const handleResetToNow = useCallback(() => {
    setIsLiveTime(true);
    setCurrentDate(new Date());
  }, []);

  // Center RA/Dec parsed cleanly to numbers (degrees)
  const raValue = useMemo(() => safeParse(viewerCenter?.ra), [viewerCenter?.ra]);
  const decValue = useMemo(() => safeParse(viewerCenter?.dec), [viewerCenter?.dec]);

  // Bundled constellation stick-figure lines: fetched once (no ra/dec/radius —
  // the whole dataset is static and small) rather than re-queried on pan/zoom.
  const { lines: constellationLines } = useConstellationLines(showConstellations);

  const { stars, targets, deepCatalogStatus, libraryTargets } = useSourceMerging(
    raValue, decValue, currentFOV, showStars,
  );

  const { location } = useObserverLocation();
  const { configuration: equipmentConfig, availableCameras, setActiveCamera } = useEquipmentConfiguration();

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
    true,
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

  const targetBrowser = useTargetBrowserItems();
  const spectralClassBrowser = useSpectralClassBrowserItems();

  // Auto-select the first target once the list loads, unless one is
  // already scoped (including via a deep link elsewhere in this file).
  useEffect(() => {
    if (!scopedTarget && targetBrowser.items.length > 0) {
      setScopedTarget(targetBrowser.items[0].id);
    }
  }, [scopedTarget, targetBrowser.items]);

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

  useEffect(() => {
    if (!selectedSource || selectedSource.type === 'alignment') return;
    let active = true;
    const controller = new AbortController();

    const fetchDetails = async () => {
      try {
        const details = await callBackend(
          'planetarium:get_visibility',
          {
            objects: [{ id: selectedSource.id, type: selectedSource.type || 'star' }],
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
              altitude: details[0].altitude,
              azimuth: details[0].azimuth,
              hourAngle: details[0].hour_angle,
              flipRequired: details[0].flip_required,
              timeToFlipSeconds: details[0].time_to_flip_seconds,
              riseTime: details[0].rise_time,
              setTime: details[0].set_time,
              transitTime: details[0].transit_time,
              aboveHorizon: details[0].above_horizon
            };
          });
        }
      } catch (error: any) {
        if (error?.name !== 'AbortError') {
          console.error("Failed to fetch detailed visibility status", error);
        }
      }
    };

    fetchDetails();
    const interval = setInterval(fetchDetails, 10000);
    return () => {
      active = false;
      controller.abort();
      clearInterval(interval);
    };
  }, [selectedSource?.id, targetTimeIso]);

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

  /**
   * Centers the sky map on and selects a star handed off from Astronomy
   * Manager's "Locate in Planetarium" action.
   *
   * Unlike handleSelectObject, this doesn't look the star up in
   * libraryTargets/targetList -- those lists are paginated/query-scoped and
   * may not contain the handed-off star, so the RA/Dec and flags carried in
   * the hand-off itself are used directly instead.
   *
   * @param {PlanetariumSource} source - The star to center on and select.
   * @returns {void}
   */
  const applyLocateStar = useCallback((source: PlanetariumSource) => {
    setViewerCenter({ ra: source.ra, dec: source.dec });
    setSelectedSource(source);
    setSelectedTargetId(source.id);
    setPendingTarget(source.id);
    setSlewRequestId(prev => prev + 1);
  }, [setSelectedTargetId, setPendingTarget]);

  /**
   * Selects a target from the target-browser primary list: scopes the
   * secondary star list to it, and centers/selects it on the map exactly
   * like picking it did before this list existed.
   *
   * @param {string} id - The target's id.
   * @returns {void}
   */
  const handleSelectScopedTarget = useCallback((id: string) => {
    setScopedTarget(id);
    handleSelectObject(id);
  }, [handleSelectObject]);

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
      hasSpectra: !!item.hasSpectra,
      hasPhotometry: !!item.hasPhotometry,
      type: 'star',
    });
  }, [scopedStarList.items, applyLocateStar]);

  // Consumes a star handed off from Astronomy Manager's "Locate in
  // Planetarium" action, routed through the shared displayCoordinator
  // navigation-intent bus so the same handoff works whether this panel is
  // already mounted or is switched to as part of the hand-off.
  useNavigationTarget('Planetarium', undefined, useCallback((intent: NavigationIntent) => {
    if (intent.action === 'locateStar' && intent.payload) {
      applyLocateStar(intent.payload as PlanetariumSource);
    }
  }, [applyLocateStar]));

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
        setIsLiveTime(false);
        // source.alignmentAttempt.timestamp is in unix epoch seconds
        setCurrentDate(new Date(source.alignmentAttempt.timestamp * 1000));
      }
    } else {
      setSelectedTargetId('');
      setPendingTarget('');
    }
  }, [setSelectedTargetId, setPendingTarget, setIsLiveTime, setCurrentDate]);

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

  // Restricts the map's rendered star field to the chosen spectral class,
  // the same class letter used to select the class's browsing list -- a
  // star the map already fetched for this viewport but that isn't
  // catalogued in that class is hidden rather than refetched.
  const visibleStars = useMemo(() => {
    if (!activeSpectralClass) return stars;
    return stars.filter((star) => spectralClassLetter(star.spectralType) === activeSpectralClass);
  }, [stars, activeSpectralClass]);

  const leftPanel = (
    <div className="astronomy-display__browser">
      <div className="astronomy-display__mode-toggle">
        <button
          type="button"
          className={`segmented-btn ${navigationMode === 'target' ? 'active' : ''}`}
          onClick={() => setNavigationMode('target')}
        >
          By target
        </button>
        <button
          type="button"
          className={`segmented-btn ${navigationMode === 'spectralClass' ? 'active' : ''}`}
          onClick={() => setNavigationMode('spectralClass')}
        >
          By spectral class
        </button>
      </div>

      {navigationMode === 'target' ? (
        <RadioListManager
          title="Targets"
          className="astronomy-display__primary-list"
          items={targetBrowser.items}
          selectedId={scopedTarget}
          pendingId={scopedTarget}
          onSelect={handleSelectScopedTarget}
          filterText={targetBrowser.filterText}
          onFilterOptionChange={() => {}}
          onFilterTextChange={targetBrowser.setFilterText}
          filterPlaceholder="Search targets..."
          highlightedIds={targetList.highlightedIds}
        />
      ) : (
        <RadioListManager
          title="Spectral classes"
          className="astronomy-display__primary-list"
          items={spectralClassBrowser.items}
          selectedId={scopedSpectralClass}
          pendingId={scopedSpectralClass}
          onSelect={setScopedSpectralClass}
          filterText={spectralClassBrowser.filterText}
          onFilterOptionChange={() => {}}
          onFilterTextChange={spectralClassBrowser.setFilterText}
          filterPlaceholder="Search classes..."
          legend={
            spectralClassBrowser.isLoading
              ? 'Scanning the catalog for spectral classes…'
              : undefined
          }
        />
      )}

      <RadioListManager
        title={
          activeSpectralClass
            ? `Class ${activeSpectralClass} stars`
            : navigationMode === 'spectralClass'
            ? 'All stars'
            : scopedTarget
            ? `Stars in ${scopedTarget}`
            : 'Select a target'
        }
        className="astronomy-display__star-list"
        items={scopedStarList.items}
        selectedId={selectedTargetId}
        pendingId={pendingTarget}
        onSelect={handleSelectScopedStar}
        onFilterOptionChange={() => {}}
        filterText={scopedStarList.filterText}
        onFilterTextChange={scopedStarList.setFilterText}
        legend={
          <>
            <span><span className="selectable-list__badge selectable-list__badge--spectra">S</span> spectrum</span>
            <span><span className="selectable-list__badge selectable-list__badge--photometry">P</span> photometry</span>
          </>
        }
        page={activeSpectralClass ? undefined : starsInScopedTarget.page}
        onPageChange={activeSpectralClass ? undefined : starsInScopedTarget.setPage}
        hasMore={activeSpectralClass ? false : starsInScopedTarget.hasMore}
      />
    </div>
  );

  const centerPanel = (
    <div className="planetarium-viewer-container">
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
        showAlignment={showAlignment}
        onToggleAlignment={setShowAlignment}
        showTrackingRisk={showTrackingRisk}
        onToggleTrackingRisk={setShowTrackingRisk}
        availableSessions={availableSessions}
        selectedSessionId={selectedSessionId}
        onSelectSession={setSelectedSessionId}
        onSyncLogs={handleSyncLogs}
        isSyncingLogs={isSyncingLogs}
        currentFOV={currentFOV}
        onOpenTimeModal={() => setIsTimeModalOpen(true)}
      />

      <CelestialSkyMap
        center={selectedSource ? { ra: selectedSource.ra, dec: selectedSource.dec } : (selectedTarget ? { ra: selectedTarget.ra, dec: selectedTarget.dec } : null)}
        sources={visibleStars}
        targets={targets}
        location={location}
        showStars={showStars}
        showFOV={showFOVOutline}
        showEnvironment={showEnvironment}
        showGrid={showGrid}
        showCatalog={showCatalog}
        showConstellations={showConstellations}
        constellationLines={constellationLines}
        showTelescope={showTelescope}
        showAlignment={showAlignment}
        showTrackingRisk={showTrackingRisk}
        alignmentAttempts={activeAlignmentAttempts}
        cumulativeTrackingAttempts={activeCumulativeTrackingAttempts}
        polarAlignment={activePolarAlignment}
        selectedSessionId={selectedSessionId}
        simulationDate={currentDate}
        fov={currentFOV}
        onFOVChange={setCurrentFOV}
        onSelectSource={handleSelectSource}
        onRightClickSource={(src, pos) => {
          setContextSource(src);
          setContextPos(pos);
        }}
        selectedTargetId={selectedTargetId}
        slewRequestId={slewRequestId}
        onCenterChange={handleCenterChange}
        sensorFovWidthDeg={equipmentConfig?.fovWidthDeg}
        sensorFovHeightDeg={equipmentConfig?.fovHeightDeg}
        plateScaleArcsecPerPx={equipmentConfig?.plateScaleArcsecPerPx}
      />

      <EquipmentConfigPanel
        configuration={equipmentConfig}
        availableCameras={availableCameras}
        onSelectCamera={setActiveCamera}
      />

      <DeepCatalogPrompt status={deepCatalogStatus} />

      <PlanetariumDateTimeModal
        isOpen={isTimeModalOpen}
        onClose={() => setIsTimeModalOpen(false)}
        currentDate={currentDate}
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

      {selectedSource && selectedSource.type !== 'alignment' && (
        <PlanetariumInfoCard
          source={selectedSource}
          observerLocation={location}
          onClose={() => handleSelectSource(null)}
        />
      )}

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
