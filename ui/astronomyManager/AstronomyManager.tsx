import React, { useState, useCallback, useMemo, useEffect } from 'react';
import { RadioListManager } from '../common/radioList/RadioListManager';
import { useSpectrumList } from './hooks/useSpectrumList';
import { useTargetBrowserItems } from './hooks/useTargetBrowserItems';
import { useSpectralClassBrowserItems, ALL_SPECTRAL_CLASSES_VALUE } from './hooks/useSpectralClassBrowserItems';
import { useStarsBySpectralClassList } from './hooks/useStarsBySpectralClassList';
import { useSpectrumData } from './hooks/useSpectrumData';
import { SpectrumViewer } from './components/SpectrumViewer';
import { PhotometryViewer } from './components/PhotometryViewer';
import { StarSummaryCard } from './components/StarSummaryCard';
import { StellarAnalysisDetails } from './components/StellarAnalysisDetails';
import { SectionPanel } from '../common/components/SectionPanel';
import { GenericDisplayLayout } from '../common/components/GenericDisplayLayout';
import { TimelineRangeSelector } from './components/TimelineRangeSelector';
import { analyzeStarPeriodicity } from '../common/services/astronomyService';
import { selectLightCurveSeries } from './utils/starDisplayFormat';
import { useReportModeReady } from '../common/utils/appBootReadiness';
import './styles/astronomyManager.css';

/**
 * AstronomyManager Component
 *
 * Main component for the astronomy display view.
 * Coordinates selection and data fetching for spectral and photometry plots.
 */
/**
 * AstronomyManager Component
 *
 * Main component for the astronomy display view.
 * Coordinates selection and data fetching for spectral and photometry plots.
 * REQ: AST-1: Data Management
 */
export const AstronomyManager: React.FC = () => {
  const [selectedSpectrum, setSelectedSpectrum] = useState<string>(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get('star') || localStorage.getItem('planetariumSelectedStar') || '';
  });
  // Seed pendingId with the same pre-selected star so useSpectrumData fetches
  // it immediately on mount -- otherwise a star arriving pre-selected (e.g.
  // via ?star= or the Planetarium hand-off) shows as selected in the list
  // but never triggers a data fetch, since fetching is driven by pendingId.
  const [pendingId, setPendingId] = useState<string>(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get('star') || localStorage.getItem('planetariumSelectedStar') || '';
  });
  const [selectedTimestamps, setSelectedTimestamps] = useState<Set<string>>(new Set());

  // Two ways to browse the catalog: drill into a target's own stars, or
  // browse by catalog spectral classification with stars ranked by how
  // well their own extracted spectrum matched. There is no unscoped "All
  // targets" -- target mode always drills into one target; "All" in
  // spectral-class mode is the equivalent whole-catalog browse.
  const [navigationMode, setNavigationMode] = useState<'target' | 'spectralClass'>('target');
  const [selectedTarget, setSelectedTarget] = useState<string>('');
  const [selectedSpectralClass, setSelectedSpectralClass] = useState<string>(ALL_SPECTRAL_CLASSES_VALUE);

  const targetBrowser = useTargetBrowserItems();
  const spectralClassBrowser = useSpectralClassBrowserItems();
  useReportModeReady('Astronomy Manager', !targetBrowser.isLoading);

  // Auto-select the first target once the list loads, unless one is
  // already chosen (including via the ?target= hand-off below).
  useEffect(() => {
    if (!selectedTarget && targetBrowser.items.length > 0) {
      setSelectedTarget(targetBrowser.items[0].id);
    }
  }, [selectedTarget, targetBrowser.items]);

  const activeTargetId = navigationMode === 'target' ? selectedTarget || undefined : undefined;
  const activeSpectralClass =
    navigationMode === 'spectralClass' && selectedSpectralClass !== ALL_SPECTRAL_CLASSES_VALUE
      ? selectedSpectralClass
      : undefined;
  const isSpectralClassScoped = !!activeSpectralClass;

  const starsByTarget = useSpectrumList(activeTargetId, undefined, pendingId, selectedSpectrum, setPendingId);
  const starsBySpectralClass = useStarsBySpectralClassList(activeSpectralClass);
  const starList = isSpectralClassScoped ? starsBySpectralClass : starsByTarget;

  const starListTitle = isSpectralClassScoped
    ? `Class ${activeSpectralClass} stars`
    : navigationMode === 'spectralClass'
    ? 'All stars'
    : activeTargetId
    ? `Stars in ${activeTargetId}`
    : 'Select a target';

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const targetParam = params.get('target');
    if (targetParam) {
      setNavigationMode('target');
      setSelectedTarget(targetParam);
    }
  }, []);

  // Picks up a star handed off from Planetarium's "Open in Astronomy Manager"
  // action while this panel is already mounted. The `?star=`/localStorage
  // read above only runs once, on this component's first mount, so a panel
  // that was visited earlier in the session needs this live event to react
  // to a later hand-off instead of missing it.
  useEffect(() => {
    const handleSelectStar = (event: Event) => {
      const starId = (event as CustomEvent<string>).detail;
      if (starId) setPendingId(starId);
    };
    window.addEventListener('astrometrics:astronomySelectStar', handleSelectStar);
    return () => window.removeEventListener('astrometrics:astronomySelectStar', handleSelectStar);
  }, []);

  /** Callback triggered when a new spectrum is successfully loaded into the viewer. */
  const handleLoaded = useCallback((id: string) => {
    setSelectedSpectrum(id);
    setPendingId('');
  }, []);

  const { astronomyData, loading, error, replaceAstronomyData } = useSpectrumData(
    pendingId,
    handleLoaded
  );

  const [isAnalyzing, setIsAnalyzing] = useState<boolean>(false);
  const [analysisError, setAnalysisError] = useState<string | null>(null);

  // A failed period search belongs to the star it ran on, so it is cleared
  // when a different star is shown.
  useEffect(() => {
    setAnalysisError(null);
  }, [astronomyData?.id]);

  /** Runs the period and transit search on the shown star and shows the result. */
  const handleAnalyze = useCallback(async () => {
    const starId = astronomyData?.id;
    if (!starId) return;
    setIsAnalyzing(true);
    setAnalysisError(null);
    try {
      const analyzedStar = await analyzeStarPeriodicity(starId);
      if (analyzedStar) replaceAstronomyData(analyzedStar);
    } catch (analysisFailure) {
      setAnalysisError(analysisFailure instanceof Error ? analysisFailure.message : String(analysisFailure));
    } finally {
      setIsAnalyzing(false);
    }
  }, [astronomyData?.id, replaceAstronomyData]);

  // A loaded star with nothing to plot gets a one-line note in place of a
  // large empty plot, so the other plot can use the room.
  const isStarLoaded = !!astronomyData && !loading && !error;
  const hasSpectrum =
    (astronomyData?.spectroscopy?.wavelengthsAngstrom?.length ?? 0) > 0 ||
    (astronomyData?.wavelength?.length ?? 0) > 0;
  const hasPhotometry = selectLightCurveSeries(astronomyData?.photometry).values.length > 0;

  // Extract all available timestamps from both photometry and spectroscopy
  // REQ: AST-1.3: The display SHALL present a timeline for time-series data.
  const availableTimestamps = useMemo(() => {
    const times = new Set<string>();
    if (astronomyData?.photometry?.timestamps) {
      astronomyData.photometry.timestamps.forEach(t => times.add(t));
    }
    const sorted = Array.from(times).sort((a, b) => new Date(a).getTime() - new Date(b).getTime());

    return sorted;
  }, [astronomyData]);

  const [startIdx, setStartIdx] = useState<number>(0);
  const [endIdx, setEndIdx] = useState<number>(0);
  const [showFeatures, setShowFeatures] = useState<boolean>(true);

  const hasFeatures =
    (astronomyData?.spectroscopy?.probableSpectralFeatures?.length ?? 0) > 0 ||
    (astronomyData?.spectroscopy?.emissionLines?.length ?? 0) > 0;

  // Reset the selected timestamp range whenever the underlying data
  // changes (e.g. a different star is picked). Gating this on "only if
  // nothing is selected yet" left a previously selected star's
  // timestamps in place after switching to a star with entirely
  // different ones, so every point got filtered out of both plots even
  // though the new star had real data.
  useEffect(() => {
    setStartIdx(0);
    setEndIdx(Math.max(0, availableTimestamps.length - 1));
    setSelectedTimestamps(new Set(availableTimestamps));
  }, [availableTimestamps]);

  const handleRangeChange = (start: number, end: number) => {
    setStartIdx(start);
    setEndIdx(end);
    const next = new Set<string>();
    for(let i=start; i<=end; i++) {
        if (availableTimestamps[i]) next.add(availableTimestamps[i]);
    }
    setSelectedTimestamps(next);
  };

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
          selectedId={selectedTarget}
          pendingId={selectedTarget}
          onSelect={setSelectedTarget}
          filterText={targetBrowser.filterText}
          onFilterOptionChange={() => {}}
          onFilterTextChange={targetBrowser.setFilterText}
          filterPlaceholder="Search targets..."
          isLoading={targetBrowser.isLoading}
          loadingMessage="Loading targets…"
          emptyMessage="No targets found."
        />
      ) : (
        <RadioListManager
          title="Spectral classes"
          className="astronomy-display__primary-list"
          items={spectralClassBrowser.items}
          selectedId={selectedSpectralClass}
          pendingId={selectedSpectralClass}
          onSelect={setSelectedSpectralClass}
          filterText={spectralClassBrowser.filterText}
          onFilterOptionChange={() => {}}
          onFilterTextChange={spectralClassBrowser.setFilterText}
          filterPlaceholder="Search classes..."
          isLoading={spectralClassBrowser.isLoading}
          loadingMessage="Scanning the catalog for spectral classes…"
          emptyMessage="No spectral classes found."
        />
      )}

      <RadioListManager
        title={starListTitle}
        className="astronomy-display__star-list"
        // REQ: AST-1.1: The display SHALL present a selectable list of all identified stellar objects.
        items={starList.items}
        selectedId={selectedSpectrum}
        pendingId={pendingId}
        onSelect={setPendingId}
        onFilterOptionChange={() => {}}
        // REQ: AST-1.2: The display SHALL allow filtering of the data list by text search.
        filterText={starList.filterText}
        onFilterTextChange={starList.setFilterText}
        legend={
          <>
            <span><span className="selectable-list__badge selectable-list__badge--spectra">S</span> spectrum</span>
            <span><span className="selectable-list__badge selectable-list__badge--photometry">P</span> photometry</span>
          </>
        }
        isLoading={starList.isLoading}
        loadingMessage="Loading stars…"
        emptyMessage="No stars found for this selection."
        page={isSpectralClassScoped ? undefined : starsByTarget.page}
        onPageChange={isSpectralClassScoped ? undefined : starsByTarget.setPage}
        hasMore={isSpectralClassScoped ? false : starsByTarget.hasMore}
        totalPages={isSpectralClassScoped ? undefined : starsByTarget.totalPages}
      />
    </div>
  );

  const rightPanel = (
    <div className="astronomy-display__right-column">
      <SectionPanel title="Timeline" className="astronomy-display__panel--compact">
        <div className="astronomy-display__timeline">
          <TimelineRangeSelector
            timestamps={availableTimestamps}
            startIdx={startIdx}
            endIdx={endIdx}
            onChange={handleRangeChange}
          />
        </div>
      </SectionPanel>
      <SectionPanel title="Stellar Analysis">
        <StellarAnalysisDetails
          astronomyData={astronomyData}
          onAnalyze={handleAnalyze}
          isAnalyzing={isAnalyzing}
          analysisError={analysisError}
        />
      </SectionPanel>
    </div>
  );

  const centerPanel = (
    <div className="astronomy-display__plots">
      <StarSummaryCard astronomyData={astronomyData} starId={selectedSpectrum || pendingId} />
      {isStarLoaded && !hasSpectrum ? (
        <SectionPanel title="Spectrum" className="astronomy-display__panel--collapsed">
          <div className="astronomy-display__collapsed-note">No spectrum has been recorded for this star.</div>
        </SectionPanel>
      ) : (
        <SectionPanel
          title="Spectrum"
          headerContent={
            hasFeatures ? (
              <div className="astronomy-viewer__segmented-control">
                <button
                  type="button"
                  className={`segmented-btn ${!showFeatures ? 'active' : ''}`}
                  onClick={() => setShowFeatures((v) => !v)}
                >
                  Hide Feature Lines
                </button>
              </div>
            ) : null
          }
        >
          <SpectrumViewer
            astronomyData={astronomyData}
            loading={loading}
            error={error}
            active={!!selectedSpectrum}
            showFeatures={showFeatures}
            onToggleFeatures={() => setShowFeatures((v) => !v)}
          />
        </SectionPanel>
      )}

      {isStarLoaded && !hasPhotometry ? (
        <SectionPanel title="Photometry" className="astronomy-display__panel--collapsed">
          <div className="astronomy-display__collapsed-note">No photometry has been recorded for this star.</div>
        </SectionPanel>
      ) : (
        <SectionPanel title="Photometry">
          {/* REQ: AST-1.5: Selecting a star SHALL simultaneously display both plots. */}
          <PhotometryViewer
            astronomyData={astronomyData}
            loading={loading}
            error={error}
            selectedTimestamps={selectedTimestamps}
          />
        </SectionPanel>
      )}
    </div>
  );

  return (
    <GenericDisplayLayout
      className="astronomy-display"
      leftPanel={leftPanel}
      centerPanel={centerPanel}
      rightPanel={rightPanel}
    />
  );
};
