/**
 * @fileoverview Decides which files an "Analyze" click sends to the backend.
 *
 * Spectral analysis has two stages. Stage one measures the master stacked
 * spectral image, which has the best signal-to-noise ratio, and sets the
 * baseline for the target's dispersion geometry, wavelengths, absorption
 * features and spectral type. Stage two follows the raw spectral frames
 * over time. Keeping the stages separate stops a leftover checkbox
 * selection of raw frames from replacing the master stack analysis.
 */

/** Filter names that mean the frames are spectroscopy frames. */
const SPECTRAL_FILTER_TYPES = ['SPEC', 'Spectroscopy', 'Star Analyzer 200'];

/** What an analysis run should be given. */
export interface AnalysisSelection {
    /** Paths of the files to analyze. */
    paths: string[];
    /** Filter type to tell the backend, or undefined to let it decide. */
    filterType?: string;
}

/** Everything the choice depends on. */
export interface AnalysisSelectionInput {
    /** The filter shared by the files in view, or null when mixed or none. */
    filterType: string | null;
    /** Path of the master stacked image, if one exists. */
    stackedImage: string | null;
    /** Path of the master stacked spectral image, if one exists. */
    stackedSpectralTarget: string | null;
    /** Paths the user ticked in the file list. */
    checkedFiles: ReadonlySet<string>;
    /** Path of the file being previewed, if any. */
    selectedFile: string | null;
    /** Paths of every file in the current view. */
    filteredFilePaths: readonly string[];
}

/**
 * Says whether a filter name means spectroscopy.
 * @param filterType The filter name, or null.
 * @returns True for a spectroscopy filter.
 */
export function isSpectralFilter(filterType: string | null | undefined): boolean {
    return filterType !== null && filterType !== undefined && SPECTRAL_FILTER_TYPES.includes(filterType);
}

/**
 * Chooses the files for the main "Analyze Target" action.
 *
 * With a spectral filter and a master stacked spectral image, the master
 * stack is always analyzed first, even when raw frames are ticked. Otherwise
 * the order is: ticked files, the master stacked image, the previewed file,
 * then every file in view.
 *
 * @param input The current selection state.
 * @returns The files and filter type to analyze, or null when there is nothing to analyze.
 */
export function chooseAnalysisSelection(input: AnalysisSelectionInput): AnalysisSelection | null {
    const filterType = input.filterType || undefined;
    if (isSpectralFilter(input.filterType) && input.stackedSpectralTarget) {
        return { paths: [input.stackedSpectralTarget], filterType: 'SPEC' };
    }
    if (input.checkedFiles.size > 0) {
        return { paths: Array.from(input.checkedFiles), filterType };
    }
    if (input.stackedImage) {
        return { paths: [input.stackedImage], filterType };
    }
    if (input.selectedFile) {
        return { paths: [input.selectedFile], filterType };
    }
    if (input.filteredFilePaths.length > 0) {
        return { paths: [...input.filteredFilePaths], filterType };
    }
    return null;
}

/**
 * Chooses the raw frames for the "Analyze Temporal Variation" action.
 *
 * This is stage two, so it is only offered for spectral frames once a
 * master stacked spectral image exists. It uses the ticked frames, or
 * every frame in view when none are ticked.
 *
 * @param input The current selection state.
 * @returns The raw frames to follow over time, or null when the action is not available.
 */
export function chooseTemporalVariationSelection(input: AnalysisSelectionInput): AnalysisSelection | null {
    if (!isSpectralFilter(input.filterType) || !input.stackedSpectralTarget) return null;
    const paths =
        input.checkedFiles.size > 0 ? Array.from(input.checkedFiles) : [...input.filteredFilePaths];
    return paths.length > 0 ? { paths, filterType: 'SPEC' } : null;
}
