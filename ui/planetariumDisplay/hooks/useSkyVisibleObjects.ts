/**
 * @file useSkyVisibleObjects.ts
 * @description Chooses what the Planetarium's sky map draws: the stars, the target markers and
 * the stack pictures of the Images layer. The sky follows the browse mode and the target list's
 * filters, so the map shows the same objects as the left column.
 */

import { useMemo } from 'react';
import { useClassStarSources, type ClassStarSourcesResult } from './useClassStarSources';
import { normalizeTargetName, type HorizonCheck } from './useTargetsBelowHorizon';
import type { StackPictureLayer } from './useStackPictures';
import type { SourceMergingResult } from './useSourceMerging';
import { buildSkyVisibleTargetKeys, filterSkyPictures, isSkyTargetShown } from '../utils/skyTargetFilter';
import type { SelectableItem } from '../../common/components/SelectableList';

/** What `useSkyVisibleObjects` needs from the display. */
export interface SkyVisibleObjectsInput {
  /** True in the By spectral class browse mode. */
  isSpectralClassMode: boolean;
  /** The picked spectral class, or undefined for all classes. */
  activeSpectralClass: string | undefined;
  /** The catalog stars in view (`useSourceMerging`). */
  stars: SourceMergingResult['stars'];
  /** The target markers in view (`useSourceMerging`). */
  targets: SourceMergingResult['targets'];
  /** The library's targets (`useSourceMerging`). */
  libraryTargets: SourceMergingResult['libraryTargets'];
  /** True when the target list's "Show" pick hides some targets. */
  isShowFilterOn: boolean;
  /** The library's target list with the shared filter and sort applied. */
  filteredEntries: SelectableItem[] | undefined;
  /** True when the "Above horizon only" box is ticked. */
  isAboveHorizonOnly: boolean;
  /** Which targets are under the horizon, or null until the site is known. */
  horizonCheck: HorizonCheck | null;
  /** Targets that stay drawn whatever the filters say (the picked and the selected one). */
  alwaysShownTargetIds: string[];
  /** The Images layer (`useStackPictures`). */
  stackPictureLayer: StackPictureLayer;
}

/** What `useSkyVisibleObjects` gives back. */
export interface SkyVisibleObjects {
  /** The spectral class view's stars, with their loading state. */
  classStars: ClassStarSourcesResult;
  /** The stars the map draws. */
  visibleStars: SourceMergingResult['stars'];
  /** The target markers the map draws. */
  visibleTargets: SourceMergingResult['targets'];
  /** The Images layer as the sky map takes it, with only the pictures of the shown targets. */
  stackPictures: {
    show: boolean;
    opacity: number;
    pictures: StackPictureLayer['pictures'];
    getDrawable: StackPictureLayer['getDrawable'];
  };
}

/**
 * Picks the stars, target markers and stack pictures the sky map draws.
 *
 * In the spectral class mode the map shows only the stars of the left list: every classed star
 * with "All", or the chosen class's stars, each in its class color. The background catalogs,
 * other library stars and the target markers are hidden; constellation lines, the grid, the
 * horizon and the telescope and field-of-view outlines stay. By target shows everything.
 *
 * The sky follows the target list's filters: the "Show" pick and "Above horizon only". The
 * search text and the sort order do not change the sky, so typing never makes it flicker. One set
 * of visible targets serves both the target circles and the Images layer. The selected target
 * stays drawn.
 *
 * @param input The browse mode, the objects in view and the filters (see `SkyVisibleObjectsInput`).
 * @returns The objects to draw (see `SkyVisibleObjects`).
 */
export const useSkyVisibleObjects = ({
  isSpectralClassMode, activeSpectralClass, stars, targets, libraryTargets, isShowFilterOn,
  filteredEntries, isAboveHorizonOnly, horizonCheck, alwaysShownTargetIds, stackPictureLayer,
}: SkyVisibleObjectsInput): SkyVisibleObjects => {
  const classStars = useClassStarSources(isSpectralClassMode, activeSpectralClass);
  const visibleStars = isSpectralClassMode ? classStars.sources : stars;
  const isHorizonFilterOn = isAboveHorizonOnly && horizonCheck !== null;
  const libraryTargetKeys = useMemo(
    () => new Set(libraryTargets.map((target) => normalizeTargetName(target.id))),
    [libraryTargets],
  );
  const skyVisibleTargetKeys = useMemo(() => buildSkyVisibleTargetKeys(
    libraryTargets.map((target) => target.id),
    isShowFilterOn && filteredEntries ? filteredEntries.flatMap((item) => [item.id, item.label]) : null,
    isHorizonFilterOn ? horizonCheck : null,
  ), [libraryTargets, isShowFilterOn, filteredEntries, isHorizonFilterOn, horizonCheck]);

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

  return { classStars, visibleStars, visibleTargets, stackPictures };
};
