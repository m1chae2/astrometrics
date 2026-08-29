import React, { useState, useCallback, useMemo, useEffect } from 'react';
import { RadioListManager } from '../common/radioList/RadioListManager';
import { useSpectrumList } from './hooks/useSpectrumList';
import { useSpectrumData } from './hooks/useSpectrumData';
import { SpectrumViewer } from './components/SpectrumViewer';
import { PhotometryViewer } from './components/PhotometryViewer';
import { StarSummaryCard } from './components/StarSummaryCard';
import { StellarAnalysisDetails } from './components/StellarAnalysisDetails';
import { SectionPanel } from '../common/components/SectionPanel';
import { GenericDisplayLayout } from '../common/components/GenericDisplayLayout';
import { TimelineRangeSelector } from './components/TimelineRangeSelector';
import './styles/astronomyDisplay.css';

/**
 * AstronomyDisplay Component
 *
 * Main component for the astronomy display view.
 * Coordinates selection and data fetching for spectral and photometry plots.
 */
/**
 * AstronomyDisplay Component
 *
 * Main component for the astronomy display view.
 * Coordinates selection and data fetching for spectral and photometry plots.
 * REQ: AST-1: Data Management
 */
export const AstronomyDisplay: React.FC = () => {
  const [selectedSpectrum, setSelectedSpectrum] = useState<string>(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get('star') || localStorage.getItem('planetariumSelectedStar') || '';
  });
  const [pendingId, setPendingId] = useState<string>('');
  const [selectedTimestamps, setSelectedTimestamps] = useState<Set<string>>(new Set());

    const {
    items,
    filterOptions,
    selectedFilterOption,
    setFilterOption,
    filterText,
    setFilterText,
    highlightedIds,
    page,
    setPage,
    hasMore,
  } = useSpectrumList(undefined, pendingId, selectedSpectrum, setPendingId);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const targetParam = params.get('target');
    if (targetParam) {
      setFilterOption(`Target: ${targetParam}`);
    }
  }, [setFilterOption]);

  /** Callback triggered when a new spectrum is successfully loaded into the viewer. */
  const handleLoaded = useCallback((id: string) => {
    setSelectedSpectrum(id);
    setPendingId('');
  }, []);

  const { astronomyData, loading, error } = useSpectrumData(
    pendingId,
    handleLoaded
  );

  // Extract all available timestamps from both photometry and spectroscopy
  // REQ: AST-1.3: The display SHALL present a timeline for time-series data.
  const availableTimestamps = useMemo(() => {
    const times = new Set<string>();
    if (astronomyData?.lightCurve?.timestamps) {
      astronomyData.lightCurve.timestamps.forEach(t => times.add(t));
    }
    if (astronomyData?.spectraHistory) {
      astronomyData.spectraHistory.forEach(s => times.add(s.timestamp));
    }
    const sorted = Array.from(times).sort((a, b) => new Date(a).getTime() - new Date(b).getTime());

    return sorted;
  }, [astronomyData]);

  const [startIdx, setStartIdx] = useState<number>(0);
  const [endIdx, setEndIdx] = useState<number>(0);

  // Sync selected timestamps when data changes
  useEffect(() => {
    if (availableTimestamps.length > 0 && selectedTimestamps.size === 0) {
      setStartIdx(0);
      setEndIdx(availableTimestamps.length - 1);
      setSelectedTimestamps(new Set(availableTimestamps));
    }
  }, [availableTimestamps, selectedTimestamps.size]);

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
    <RadioListManager
      title="Identified Stars"
      // REQ: AST-1.1: The display SHALL present a selectable list of all identified stellar objects.
      items={items}
      selectedId={selectedSpectrum}
      pendingId={pendingId}
      onSelect={setPendingId}
      filterOptions={filterOptions}
      selectedFilterOption={selectedFilterOption}
      onFilterOptionChange={setFilterOption}
      // REQ: AST-1.2: The display SHALL allow filtering of the data list by text search.
      filterText={filterText}
      onFilterTextChange={setFilterText}
      highlightedIds={highlightedIds}
      page={page}
      onPageChange={setPage}
      hasMore={hasMore}
    />
  );

  const rightPanel = (
    <div className="astronomy-display__right-column">
      <SectionPanel title="Timeline">
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
        <StellarAnalysisDetails astronomyData={astronomyData} />
      </SectionPanel>
    </div>
  );

  const centerPanel = (
    <div className="astronomy-display__plots">
      <StarSummaryCard astronomyData={astronomyData} starId={selectedSpectrum || pendingId} />
      <SectionPanel title="Spectrum">
        <SpectrumViewer
          astronomyData={astronomyData}
          loading={loading}
          error={error}
          active={!!selectedSpectrum}
          selectedTimestamps={selectedTimestamps}
        />
      </SectionPanel>

      <SectionPanel title="Photometry">
        {/* REQ: AST-1.5: Selecting a star SHALL simultaneously display both plots. */}
        <PhotometryViewer
          astronomyData={astronomyData}
          loading={loading}
          error={error}
          selectedTimestamps={selectedTimestamps}
        />
      </SectionPanel>
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
