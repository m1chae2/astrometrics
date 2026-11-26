/**
 * @module PlanetariumBrowserColumn
 * @fileoverview The Planetarium's left column: the By target / By spectral class tabs, the target
 * (or class) list on level 1 and the picked entry's star list on level 2. The column folds to a
 * thin strip, and the choice is remembered for this viewer.
 */

import React, { useCallback } from 'react';
import { QueryClientContext } from '@tanstack/react-query';
import { RadioListManager } from '../../common/radioList/RadioListManager';
import { CollapsibleColumn } from '../../common/components/CollapsibleColumn';
import { Tabs } from '../../common/components/Tabs';
import { Checkbox } from '../../common/components/Checkbox';
import { Button } from '../../common/components/Button';
import { PopoverSelect } from '../../common/components/PopoverSelect';
import type { SelectableItem } from '../../common/components/SelectableList';
import type { TargetListFilterPanelProps } from '../../common/radioList/TargetListFilterPanel';
import type { SortChoice } from '../../common/hooks/targetListFiltering';
import { usePersistentFlag } from '../../common/hooks/usePersistentFlag';
import { prefetchAstronomyList } from '../../common/queries/useAstronomyListQuery';
import { TARGET_SORT_OPTIONS, type TargetListFilter } from '../hooks/useTargetListFilter';
import type { BrowseMode, DrillInColumn } from '../hooks/useDrillInColumn';
import '../../common/styles/segmentedToggle.css';

// Page size of a target's star list; the same as useSpectrumList's, so a
// prefetch fills the page the list will ask for.
const STAR_LIST_PAGE_SIZE = 100;

/** A browsable list as the target and class browsers give it. */
interface BrowsableList {
  items: SelectableItem[];
  filterText: string;
  setFilterText: (text: string) => void;
  isLoading: boolean;
  isError: boolean;
  retry: () => void;
}

/** The paging of a target's star list (`useSpectrumList`). */
interface StarListPaging {
  page: number;
  setPage: (page: number) => void;
  hasMore: boolean;
  totalPages?: number;
}

/** Props for PlanetariumBrowserColumn. */
interface Props {
  /** The browse mode shown now. */
  navigationMode: BrowseMode;
  /** Changes the browse mode. */
  onNavigationModeChange: (mode: BrowseMode) => void;
  /** The two levels' state and handlers (`useDrillInColumn`). */
  drill: DrillInColumn;
  /** The target browser (`useTargetBrowserItems`), for its search, loading state and labels. */
  targetBrowser: BrowsableList;
  /** The target rows to show, after the filters. */
  targetFilter: TargetListFilter;
  /** The shared target filter, or undefined while it loads. */
  filterPanel: TargetListFilterPanelProps | undefined;
  /** True once the site is known, so the horizon box can be used. */
  canFilterByHorizon: boolean;
  /** True when the planning time is Now. */
  isLiveTime: boolean;
  /** Target ids to highlight (targets the observatory computer has frames for). */
  highlightedIds: Set<string> | undefined;
  /** The target picked on level 1. */
  scopedTarget: string;
  /** Picks a target on level 1. */
  onSelectTarget: (id: string) => void;
  /** The spectral class browser (`useSpectralClassBrowserItems`). */
  spectralClassBrowser: BrowsableList;
  /** The class picked on level 1. */
  scopedSpectralClass: string;
  /** The picked spectral class, or undefined for all classes (only in the class mode). */
  activeSpectralClass: string | undefined;
  /** Picks a class on level 1. */
  onSelectSpectralClass: (id: string) => void;
  /** The star list on level 2. */
  starList: BrowsableList;
  /** The paging of a target's star list; a class's list has no pages. */
  starListPaging: StarListPaging;
  /** The object selected on the sky map. */
  selectedId: string | undefined;
  /** The object waiting to be selected. */
  pendingId: string | undefined;
  /** Picks a star on level 2. */
  onSelectStar: (id: string) => void;
}

/**
 * The left column of the Planetarium.
 *
 * @func PlanetariumBrowserColumn
 * @param {Props} props - Component props.
 * @returns {React.ReactElement} The rendered column.
 */
export const PlanetariumBrowserColumn: React.FC<Props> = ({
  navigationMode, onNavigationModeChange,
  drill: {
    isDrilledIn, levelOnePaneRef, levelTwoPaneRef, backButtonRef,
    handleBackToLevelOne, handleLevelOneKeyDown, handleLevelTwoKeyDown,
  },
  targetBrowser, targetFilter, filterPanel,
  canFilterByHorizon, isLiveTime, highlightedIds, scopedTarget, onSelectTarget,
  spectralClassBrowser, scopedSpectralClass, activeSpectralClass, onSelectSpectralClass,
  starList, starListPaging, selectedId, pendingId, onSelectStar,
}) => {
  // The whole left column folds to a thin strip, like the Astronomy Manager's; the choice is remembered for this viewer.
  const [browserCollapsed, setBrowserCollapsed] = usePersistentFlag('astrometrics:planetarium:targetListCollapsed', false);
  const browserStripTitle = navigationMode === 'target' ? 'Targets' : 'Spectral classes';
  const browserSelectedLabel = navigationMode === 'target'
    ? targetBrowser.items.find((item) => item.id === scopedTarget)?.label
    : spectralClassBrowser.items.find((item) => item.id === scopedSpectralClass)?.id;
  // The star picked in the star list, shown on the strip after the target or class.
  const browserSelectedStar = starList.items.find((item) => item.value === selectedId)?.label;

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

  // The row under the target search: a "Show" picker (a catalog, or the
  // targets one camera imaged) and a "Sort" picker. Both read and set the
  // shared target filter, the same choices Image Processing's Filter Targets
  // panel shows; only the look differs.
  const targetFilterRow = filterPanel ? (
    <>
      <PopoverSelect
        label="Show"
        size="compact"
        options={targetFilter.showOptions}
        selectedId={targetFilter.showSelectedId}
        onSelect={targetFilter.handleShowSelect}
      />
      <PopoverSelect
        label="Sort"
        size="compact"
        options={TARGET_SORT_OPTIONS}
        selectedId={filterPanel.sort}
        onSelect={(sortId) => { if (sortId) filterPanel.onSortChange(sortId as SortChoice); }}
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
      items={targetFilter.targetListItems}
      selectedId={scopedTarget}
      pendingId={scopedTarget}
      onSelect={onSelectTarget}
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
            checked={targetFilter.isAboveHorizonOnly}
            onChange={targetFilter.handleAboveHorizonOnlyChange}
            disabled={!canFilterByHorizon}
            title={isLiveTime
              ? 'Show only the targets that are above the horizon now'
              : 'Show only the targets that are above the horizon at the planning time'}
          />
        </span>
      }
      highlightedIds={highlightedIds}
      isLoading={targetBrowser.isLoading}
      loadingMessage="Loading targets"
      emptyMessage={targetFilter.emptyMessage}
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
      onSelect={onSelectSpectralClass}
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
      items={starList.items}
      selectedId={selectedId}
      pendingId={pendingId}
      onSelect={onSelectStar}
      onFilterOptionChange={() => {}}
      filterText={starList.filterText}
      onFilterTextChange={starList.setFilterText}
      filterPlaceholder="Search stars..."
      legend={
        <>
          <span><span className="selectable-list__badge selectable-list__badge--spectra">S</span> spectrum</span>
          <span><span className="selectable-list__badge selectable-list__badge--photometry">P</span> photometry</span>
        </>
      }
      isLoading={starList.isLoading}
      loadingMessage="Loading stars"
      emptyMessage="No stars found for this selection."
      isError={starList.isError}
      errorMessage="Could not load the stars."
      onRetry={starList.retry}
      page={activeSpectralClass ? undefined : starListPaging.page}
      onPageChange={activeSpectralClass ? undefined : starListPaging.setPage}
      hasMore={activeSpectralClass ? false : starListPaging.hasMore}
      totalPages={activeSpectralClass ? undefined : starListPaging.totalPages}
    />
  );

  return (
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
            onChange={(modeId) => onNavigationModeChange(modeId as BrowseMode)}
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
};
