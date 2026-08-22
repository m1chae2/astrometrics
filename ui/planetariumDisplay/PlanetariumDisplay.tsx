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
import { PlanetariumToolbar } from './components/PlanetariumToolbar';
import { PlanetariumContextMenu } from './components/PlanetariumContextMenu';
import { PlanetariumDateTimeModal } from './components/PlanetariumDateTimeModal';
import { usePlanetariumSources } from './hooks/usePlanetariumSources';
import { useOnlineCatalogSources } from './hooks/useOnlineCatalogSources';
import { useConstellationLines } from './hooks/useConstellationLines';
import { usePlanetariumTargets } from './hooks/usePlanetariumTargets';
import { useObserverLocation } from './hooks/useObserverLocation';
import { useOverlayToggles } from './hooks/useOverlayToggles';
import { useEquipmentConfiguration } from './hooks/useEquipmentConfiguration';
import { EquipmentConfigPanel } from './components/EquipmentConfigPanel';
import { PlanetariumSource, PlanetariumTarget } from '../common/types/planetariumTypes';
import { useSpectrumData } from '../astronomyDisplay/hooks/useSpectrumData';
import { SpectrumViewer } from '../astronomyDisplay/components/SpectrumViewer';
import { PhotometryViewer } from '../astronomyDisplay/components/PhotometryViewer';
import { callBackend } from '../common/services/backendApi';
import { useTargetContext } from '../common/context/TargetContext';
import { useTargetListLogic } from '../common/hooks/useTargetListLogic';
import { useRemoteStatusContext } from '../common/context/RemoteStatusContext';
import { useTelescopeStatus } from '../common/hooks/useTelescopeStatus';
import { safeParse } from './utils/coordinateUtils';
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

  // Get telescope connection status
  const { telescopeConnection } = useTelescopeStatus();

  // All overlay and plot visibility toggles
  const {
    showStars, setShowStars,
    showFOVOutline, setShowFOVOutline,
    showFITSOverlays, setShowFITSOverlays,
    showEnvironment, setShowEnvironment,
    showGrid, setShowGrid,
    showCatalog, setShowCatalog,
    showConstellations, setShowConstellations,
    showTelescope, setShowTelescope,
    showSpectraPlot, setShowSpectraPlot,
    showPhotometryPlot, setShowPhotometryPlot,
  } = useOverlayToggles();

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

  // Extend query radius 1.5× beyond FOV to preload sources at pan edges; minimum 0.5°
  const queryRadius = useMemo(() => Math.max(currentFOV * 1.5, 0.5), [currentFOV]);

  const { sources: localSources } = usePlanetariumSources(raValue, decValue, queryRadius);

  // Whole-sky bright-star query: served from the locally bundled Hipparcos
  // extract ('hipparcos') rather than a live query. GAIA DR3 is unsuitable
  // for this layer — its detectors saturate on very bright stars, so it's
  // missing nearly every naked-eye-famous star.
  //
  // This driver's backend query is a fast in-memory numpy filter over a
  // bundled extract (no network round trip) and its maximum_query_radius_degrees
  // is 180 — a true whole sky. So rather than rescoping to the viewport on
  // every pan (which meant re-querying, and a brief empty gap, every time you
  // panned into a not-yet-queried region, including areas hidden behind the
  // ground/horizon overlay that later rotate into view), we query the entire
  // sky once with a fixed center/radius. The backend's brightest-5000 cap
  // still applies, so the overlay is always handed the 5000 brightest stars
  // in the sky, not 83k.
  const brightStarDrivers = useMemo(() => ['hipparcos'], []);
  const { onlineSources: brightStarSources } = useOnlineCatalogSources(
    0, 0, 180,
    brightStarDrivers,
    true,
  );

  // Deep-zoom star query: GAIA DR3 supplies stars fainter than Hipparcos's
  // ~magnitude-12 completeness limit (see MAX_ZOOM_LIMITING_MAGNITUDE),
  // scoped to the current viewport rather than the whole sky since GAIA's
  // per-region density is far higher than Hipparcos's. Gated on showStars
  // so toggling the background field off also stops these network queries.
  const deepStarDrivers = useMemo(() => ['gaia'], []);
  const { onlineSources: deepStarSources } = useOnlineCatalogSources(
    raValue, decValue, queryRadius,
    deepStarDrivers,
    showStars,
  );

  // Bundled constellation stick-figure lines: fetched once (no ra/dec/radius —
  // the whole dataset is static and small) rather than re-queried on pan/zoom.
  const { lines: constellationLines } = useConstellationLines(showConstellations);

  const sources = useMemo(() => {
    const localIds = new Set(localSources.map(source => source.id));
    const combinedOnline = [
      ...brightStarSources,
      ...deepStarSources.filter(source => !localIds.has(source.id)),
    ];
    const seenOnlineIds = new Set<string>();
    const dedupedOnline = combinedOnline.filter(source => {
      if (localIds.has(source.id) || seenOnlineIds.has(source.id)) return false;
      seenOnlineIds.add(source.id);
      return true;
    });
    return [...localSources, ...dedupedOnline];
  }, [localSources, brightStarSources, deepStarSources]);

  const { location } = useObserverLocation();
  const { targets: libraryTargets } = usePlanetariumTargets();
  const { configuration: equipmentConfig, availableCameras, setActiveCamera } = useEquipmentConfiguration();

  // Split sources into stars and targets
  const stars = useMemo(() => sources.filter(source => source.type === 'star'), [sources]);
  const targets = useMemo(() => sources.filter(source => source.type === 'target') as PlanetariumTarget[], [sources]);

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

  // Sync details from Backend for selectedSource visibility status
  useEffect(() => {
    if (!selectedSource) return;
    let active = true;
    const fetchDetails = async () => {
      try {
        const details = await callBackend('planetarium:get_visibility', {
          objects: [{ id: selectedSource.id, type: selectedSource.type || 'star' }],
          time: currentDate.toISOString()
        });
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
      } catch (error) {
        console.error("Failed to fetch detailed visibility status", error);
      }
    };
    fetchDetails();
    const interval = setInterval(fetchDetails, 10000);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, [selectedSource?.id, currentDate]);

  // Load physical spectra/photometry data for the selected star
  const { astronomyData, loading: plotLoading, error: plotError } = useSpectrumData(
    selectedSource?.id || ''
  );

  const availableTimestamps = useMemo(() => {
    const times = new Set<string>();
    if (astronomyData?.lightCurve?.timestamps) {
      astronomyData.lightCurve.timestamps.forEach(t => times.add(t));
    }
    if (astronomyData?.spectraHistory) {
      astronomyData.spectraHistory.forEach(s => times.add(s.timestamp));
    }
    return new Set(Array.from(times).sort((a, b) => new Date(a).getTime() - new Date(b).getTime()));
  }, [astronomyData]);

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
   * Handles source selection from the canvas, resetting plot visibility toggles.
   *
   * @param {PlanetariumSource | null} source - The selected source, or null to deselect.
   * @returns {void}
   */
  const handleSelectSource = useCallback((source: PlanetariumSource | null) => {
    setSelectedSource(source);
    setShowSpectraPlot(false);
    setShowPhotometryPlot(false);
    if (source) {
      setSelectedTargetId(source.id);
      setPendingTarget(source.id);
    } else {
      setSelectedTargetId('');
      setPendingTarget('');
    }
  }, [setShowSpectraPlot, setShowPhotometryPlot, setSelectedTargetId, setPendingTarget]);

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

  const leftPanel = (
    <RadioListManager
      className="planetarium-display__left"
      title="Targets"
      items={targetList.items}
      selectedId={selectedTargetId}
      pendingId={pendingTarget}
      onSelect={handleSelectObject}
      filterOptions={targetList.filterOptions}
      selectedFilterOption={targetList.selectedFilterOption}
      onFilterOptionChange={targetList.setFilterOption}
      filterText={targetList.filterText}
      onFilterTextChange={targetList.setFilterText}
      highlightedIds={targetList.highlightedIds}
    />
  );

  const centerPanel = (
    <div className="planetarium-viewer-container">
      <PlanetariumToolbar
        showStars={showStars}
        onToggleStars={setShowStars}
        showFOV={showFOVOutline}
        onToggleFOV={setShowFOVOutline}
        showFITS={showFITSOverlays}
        onToggleFITS={setShowFITSOverlays}
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
        currentFOV={currentFOV}
        onOpenTimeModal={() => setIsTimeModalOpen(true)}
      />

      <CelestialSkyMap
        center={selectedSource ? { ra: selectedSource.ra, dec: selectedSource.dec } : (selectedTarget ? { ra: selectedTarget.ra, dec: selectedTarget.dec } : null)}
        sources={stars}
        targets={targets}
        location={location}
        showStars={showStars}
        showFOV={showFOVOutline}
        showFITS={showFITSOverlays}
        showEnvironment={showEnvironment}
        showGrid={showGrid}
        showCatalog={showCatalog}
        showConstellations={showConstellations}
        constellationLines={constellationLines}
        showTelescope={showTelescope}
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
        sensorFovWidthDeg={equipmentConfig?.fov_width_deg}
        sensorFovHeightDeg={equipmentConfig?.fov_height_deg}
      />

      <EquipmentConfigPanel
        configuration={equipmentConfig}
        availableCameras={availableCameras}
        onSelectCamera={setActiveCamera}
      />

      <PlanetariumDateTimeModal
        isOpen={isTimeModalOpen}
        onClose={() => setIsTimeModalOpen(false)}
        currentDate={currentDate}
        onDateChange={handleDateChange}
        onResetToNow={handleResetToNow}
      />

      {selectedSource && (
        <PlanetariumInfoCard
          source={selectedSource}
          observerLocation={location}
          onClose={() => handleSelectSource(null)}
          showSpectraPlot={showSpectraPlot}
          onShowSpectraPlotChange={setShowSpectraPlot}
          showPhotometryPlot={showPhotometryPlot}
          onShowPhotometryPlotChange={setShowPhotometryPlot}
          astronomyData={astronomyData}
          plotLoading={plotLoading}
          plotError={plotError}
        />
      )}

      {/* Floating Scientific Plot Panel (Spectroscopy / Photometry) */}
      {(showSpectraPlot || showPhotometryPlot) && selectedSource && (
        <div className="planetarium-plot-panel">
          <div className="planetarium-plot-panel__header">
            <h4>{selectedSource.name} Scientific Data</h4>
            <div className="planetarium-plot-panel__controls">
              <button
                className="planetarium-plot-panel__close"
                onClick={() => {
                  setShowSpectraPlot(false);
                  setShowPhotometryPlot(false);
                }}
              >
                &times;
              </button>
            </div>
          </div>
          <div className="planetarium-plot-panel__body">
            {showSpectraPlot && (
              <div className="planetarium-plot-section">
                <h5>Spectroscopy Calibration Curve</h5>
                <SpectrumViewer
                  astronomyData={astronomyData}
                  loading={plotLoading}
                  error={plotError}
                  active={!!selectedSource}
                  selectedTimestamps={availableTimestamps}
                />
              </div>
            )}
            {showPhotometryPlot && (
              <div className="planetarium-plot-section">
                <h5>Photometry Time-Series Curves</h5>
                <PhotometryViewer
                  astronomyData={astronomyData}
                  loading={plotLoading}
                  error={plotError}
                  selectedTimestamps={availableTimestamps}
                />
              </div>
            )}
          </div>
        </div>
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
