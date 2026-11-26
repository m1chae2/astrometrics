/**
 * @file useTargetListFilter.ts
 * @description The Planetarium's target list filters: the "Show" and "Sort" pickers (the shared
 * target filter, the same choices as Image Processing's Filter Targets panel), the "Below" horizon
 * badge, and the "Only targets above the horizon" box. It turns the library's target list into the
 * rows the left column shows.
 */

import React, { useCallback, useMemo } from 'react';
import { normalizeTargetName, type HorizonCheck } from './useTargetsBelowHorizon';
import { usePersistentFlag } from '../../common/hooks/usePersistentFlag';
import type { PopoverSelectOption } from '../../common/components/PopoverSelect';
import type { SelectableItem } from '../../common/components/SelectableList';
import type { TargetListFilterPanelProps } from '../../common/radioList/TargetListFilterPanel';
import {
  CAMERA_ALL, CATALOG_ALL, CATALOG_MESSIER, CATALOG_NGC_IC, CATALOG_NO_IMAGE, CATALOG_PLANETS, CATALOG_STARS,
  SORT_ALPHABETICAL, SORT_NEWEST,
} from '../../common/hooks/targetListFiltering';

// Browser storage key for the "Only targets above the horizon" choice, and the key it was saved
// under before keys took the `astrometrics:` form. It is a per-viewer convenience, so the list
// still works when storage is blocked.
const ABOVE_HORIZON_ONLY_STORAGE_KEY = 'astrometrics:planetarium:aboveHorizonOnly';
const ABOVE_HORIZON_ONLY_LEGACY_KEY = 'planetarium.targetList.aboveHorizonOnly';

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

/** The "Sort" picker's choices. */
export const TARGET_SORT_OPTIONS: PopoverSelectOption[] = [
  { id: SORT_NEWEST, label: 'Newest', title: 'Most recently imaged first' },
  { id: SORT_ALPHABETICAL, label: 'A–Z', title: 'By name' },
];

/** What `useTargetListFilter` needs from the display. */
export interface TargetListFilterInput {
  /** The shared target filter's choices and setters (`useTargetListLogic().filterPanel`). */
  filterPanel: TargetListFilterPanelProps | undefined;
  /** The library's target list with the shared filter and sort applied. */
  filteredEntries: SelectableItem[] | undefined;
  /** The target browser's rows (with star counts), in their own order. */
  browserItems: SelectableItem[];
  /** Which targets are under the horizon, or null until the site is known. */
  horizonCheck: HorizonCheck | null;
  /** True when the planning time is Now. */
  isLiveTime: boolean;
}

/** What `useTargetListFilter` gives back. */
export interface TargetListFilter {
  /** True when the "Above horizon only" box is ticked. */
  isAboveHorizonOnly: boolean;
  /** Ticks or clears the "Above horizon only" box and saves the choice for this viewer. */
  handleAboveHorizonOnlyChange: (event: React.ChangeEvent<HTMLInputElement>) => void;
  /** The "Show" picker's menu. */
  showOptions: PopoverSelectOption[];
  /** The "Show" picker's selected entry, or null while the filter has not loaded. */
  showSelectedId: string | null;
  /** Applies a "Show" pick. */
  handleShowSelect: (showId: string | null) => void;
  /** True when the "Show" pick hides some targets (a catalog or a camera is set). */
  isShowFilterOn: boolean;
  /** The target rows after the shared filter and sort, before the horizon box. */
  filteredTargetItems: SelectableItem[];
  /** The target rows the list shows: with the "Below" badge, and without the hidden ones. */
  targetListItems: SelectableItem[];
  /** Says why the target list is empty. */
  emptyMessage: string;
}

/**
 * Applies the shared target filter and the horizon box to the Planetarium's target list.
 *
 * @param input The filter, the rows and the horizon check (see `TargetListFilterInput`).
 * @returns The rows to show, the pickers' state and the horizon box (see `TargetListFilter`).
 */
export const useTargetListFilter = ({
  filterPanel, filteredEntries, browserItems, horizonCheck, isLiveTime,
}: TargetListFilterInput): TargetListFilter => {
  const [isAboveHorizonOnly, setIsAboveHorizonOnly] = usePersistentFlag(
    ABOVE_HORIZON_ONLY_STORAGE_KEY, false, { legacyKey: ABOVE_HORIZON_ONLY_LEGACY_KEY });

  /**
   * Turns the "Only targets above the horizon" filter on or off and saves the choice for this viewer.
   *
   * @param event The checkbox change.
   */
  const handleAboveHorizonOnlyChange = useCallback((event: React.ChangeEvent<HTMLInputElement>) => {
    setIsAboveHorizonOnly(event.target.checked);
  }, [setIsAboveHorizonOnly]);

  // `filteredEntries` is the library's target list with the shared filter and
  // sort applied; the rows themselves come from the target browser (with star
  // counts), put in the filtered order.
  const isTargetFilterInUse = !!filterPanel
    && (filterPanel.selectedCatalog !== CATALOG_ALL
      || filterPanel.selectedCamera !== CAMERA_ALL
      || filterPanel.sort !== SORT_ALPHABETICAL);

  // The "Show" picker's menu: the catalogs, then one entry per camera. One
  // pick sets one of the two shared filters and clears the other. When both
  // are set (from another screen), an extra first entry names the pair.
  const showOptions = useMemo<PopoverSelectOption[]>(() => {
    if (!filterPanel) return [];
    const catalogOptions = filterPanel.catalogOptions.map((catalog) => ({
      id: `${SHOW_CATALOG_PREFIX}${catalog}`,
      label: CATALOG_SHORT_LABELS[catalog] ?? catalog,
      group: 'Catalog',
    }));
    const cameraOptions = filterPanel.cameras.map((camera) => ({
      id: `${SHOW_CAMERA_PREFIX}${camera.name}`,
      label: camera.name,
      detail: camera.targetCount === 0
        ? 'no images yet'
        : `${camera.targetCount} ${camera.targetCount === 1 ? 'target' : 'targets'}`,
      group: 'Imaged with',
    }));
    const isBothSet = filterPanel.selectedCatalog !== CATALOG_ALL && filterPanel.selectedCamera !== CAMERA_ALL;
    const pairOption = isBothSet
      ? [{
        id: SHOW_PAIR_ID,
        label: `${CATALOG_SHORT_LABELS[filterPanel.selectedCatalog] ?? filterPanel.selectedCatalog} · ${filterPanel.selectedCamera}`,
        group: 'Current',
      }]
      : [];
    return [...pairOption, ...catalogOptions, ...cameraOptions];
  }, [filterPanel]);

  const showSelectedId = !filterPanel
    ? null
    : filterPanel.selectedCatalog !== CATALOG_ALL && filterPanel.selectedCamera !== CAMERA_ALL
    ? SHOW_PAIR_ID
    : filterPanel.selectedCamera !== CAMERA_ALL
    ? `${SHOW_CAMERA_PREFIX}${filterPanel.selectedCamera}`
    : `${SHOW_CATALOG_PREFIX}${filterPanel.selectedCatalog}`;

  /**
   * Applies a "Show" pick: a catalog clears the camera filter, a camera clears the catalog filter.
   *
   * @param showId The picked entry's id.
   */
  const handleShowSelect = useCallback((showId: string | null) => {
    if (!filterPanel || !showId || showId === SHOW_PAIR_ID) return;
    if (showId.startsWith(SHOW_CAMERA_PREFIX)) {
      filterPanel.onCameraChange(showId.slice(SHOW_CAMERA_PREFIX.length));
      filterPanel.onCatalogChange(CATALOG_ALL);
    } else {
      filterPanel.onCatalogChange(showId.slice(SHOW_CATALOG_PREFIX.length));
      filterPanel.onCameraChange(CAMERA_ALL);
    }
  }, [filterPanel]);

  const filteredTargetItems = useMemo(() => {
    if (!isTargetFilterInUse || !filteredEntries) return browserItems;
    const browserItemsByName = new Map(browserItems.map((item) => [normalizeTargetName(item.id), item]));
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
  }, [browserItems, filteredEntries, isTargetFilterInUse]);

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

  const isShowFilterOn = !!filterPanel
    && (filterPanel.selectedCatalog !== CATALOG_ALL || filterPanel.selectedCamera !== CAMERA_ALL);

  // Says why the target list is empty: nothing loaded, the shared filter, or the horizon box.
  const emptyMessage = browserItems.length === 0
    ? 'No targets found.'
    : isAboveHorizonOnly && horizonCheck && filteredTargetItems.length > 0
    ? 'No targets are above the horizon.'
    : 'No targets match the filter.';

  return {
    isAboveHorizonOnly,
    handleAboveHorizonOnlyChange,
    showOptions,
    showSelectedId,
    handleShowSelect,
    isShowFilterOn,
    filteredTargetItems,
    targetListItems,
    emptyMessage,
  };
};
