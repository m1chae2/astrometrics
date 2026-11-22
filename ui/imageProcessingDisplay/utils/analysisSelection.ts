/**
 * @fileoverview Decides which files an "Analyze" click sends to the backend.
 *
 * A master stack of the needed kind is always used when it exists. With no such stack, the file
 * selected in the file browser (the previewed file) is used.
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
    /** The filter of the previewed file, or null when it is unknown. */
    selectedFileFilter?: string | null;
}

/**
 * Says whether a filter name means spectroscopy.
 * @param filterType The filter name, or null.
 * @returns True for a spectroscopy filter.
 */
export function isSpectralFilter(filterType: string | null | undefined): boolean {
    return filterType !== null && filterType !== undefined && SPECTRAL_FILTER_TYPES.includes(filterType);
}

/** The analysis stages the Analyze Target menu can run by name. */
export type AnalysisStage = 'astrometry' | 'photometry' | 'spectroscopy';

/** The kind of frame an analysis stage works on. */
export type AnalysisFrameKind = 'imaging' | 'spectral';

/** Whether one menu stage can run now, and why not when it cannot. */
export interface StageAvailability {
    /** True when the stage can be started. */
    enabled: boolean;
    /** The reason the stage is off, for a tooltip. Empty when the stage is on. */
    reason: string;
}

/** The reason shown when the main Analyze Target click has no stack and no selected file. */
export const NO_SOURCE_TO_ANALYZE_REASON = 'Stack the frames or select a file in the file list first';

/** The reason shown when astrometry or photometry has nothing to work on. */
export const NO_IMAGING_SOURCE_REASON = 'Stack the luminance frames or select a frame first';

/** The reason shown when spectroscopy has no spectral image to work on. */
export const NO_SPECTRAL_SOURCE_REASON = 'Stack the spectral frames or select a spectral frame first';

/** The reason shown when spectroscopy cannot get the astrometry it needs first. */
export const NO_ASTROMETRY_SOURCE_REASON =
    'Spectroscopy needs astrometry first. Stack the luminance frames or select a frame first';

/**
 * Says what kind of frame the selected file is.
 *
 * The selected file is the one the user picked in the file browser (the file in the viewer).
 * It is a spectral frame when it is the master spectral stack or its filter is a spectral filter.
 *
 * @param input The current selection state.
 * @returns 'spectral', 'imaging', or null when no file is selected.
 */
export function selectedFileKind(input: AnalysisSelectionInput): AnalysisFrameKind | null {
    if (!input.selectedFile) return null;
    if (input.selectedFile === input.stackedSpectralTarget) return 'spectral';
    if (input.selectedFile === input.stackedImage) return 'imaging';
    return isSpectralFilter(input.selectedFileFilter) ? 'spectral' : 'imaging';
}

/**
 * Picks the one file an analysis of a given kind should be given.
 *
 * A master stack of the needed kind always wins, whatever is ticked or previewed.
 * With no such stack, the selected file is used, but only when it is the right kind.
 *
 * @param input The current selection state.
 * @param kind The kind of frame the analysis works on.
 * @returns The path to send, or null when there is nothing to analyze.
 */
export function chooseSourceFile(input: AnalysisSelectionInput, kind: AnalysisFrameKind): string | null {
    const stack = kind === 'spectral' ? input.stackedSpectralTarget : input.stackedImage;
    if (stack) return stack;
    return selectedFileKind(input) === kind ? input.selectedFile : null;
}

/**
 * Says which menu stages can run now.
 *
 * Astrometry and photometry need an imaging stack or a selected imaging frame. Spectroscopy needs
 * a spectral stack or a selected spectral frame, and also needs astrometry to be able to run.
 *
 * @param input The current selection state.
 * @returns For each stage, whether it is on and the reason when it is off.
 */
export function describeStageAvailability(input: AnalysisSelectionInput): Record<AnalysisStage, StageAvailability> {
    const canRunAstrometry = chooseSourceFile(input, 'imaging') !== null;
    const hasSpectralSource = chooseSourceFile(input, 'spectral') !== null;
    const imagingState: StageAvailability = canRunAstrometry
        ? { enabled: true, reason: '' }
        : { enabled: false, reason: NO_IMAGING_SOURCE_REASON };
    let spectroscopyState: StageAvailability = { enabled: true, reason: '' };
    if (!hasSpectralSource) {
        spectroscopyState = { enabled: false, reason: NO_SPECTRAL_SOURCE_REASON };
    } else if (!canRunAstrometry) {
        spectroscopyState = { enabled: false, reason: NO_ASTROMETRY_SOURCE_REASON };
    }
    return { astrometry: imagingState, photometry: imagingState, spectroscopy: spectroscopyState };
}

/**
 * Chooses the file for one named stage of the Analyze Target menu.
 *
 * @param input The current selection state.
 * @param stage The stage the user picked.
 * @returns The file and filter type to send, or null when the stage cannot run.
 */
export function chooseStageSelection(input: AnalysisSelectionInput, stage: AnalysisStage): AnalysisSelection | null {
    if (!describeStageAvailability(input)[stage].enabled) return null;
    if (stage === 'spectroscopy') {
        const path = chooseSourceFile(input, 'spectral');
        return path ? { paths: [path], filterType: 'SPEC' } : null;
    }
    const path = chooseSourceFile(input, 'imaging');
    const filterType = isSpectralFilter(input.filterType) ? undefined : input.filterType || undefined;
    return path ? { paths: [path], filterType } : null;
}

/**
 * Chooses the file for the main "Analyze Target" click.
 *
 * With a spectral filter in view, the click analyzes spectra; otherwise it measures stars (photometry).
 * Either way the master stack of that kind is always used when it exists, whatever is ticked or
 * previewed. With no such stack, the selected file is used. With neither, nothing is returned and
 * the action is off. The backend reports a stage it had to skip (for example, no astrometry) in the job message.
 *
 * @param input The current selection state.
 * @returns The files and filter type to analyze, or null when there is nothing to analyze.
 */
export function chooseAnalysisSelection(input: AnalysisSelectionInput): AnalysisSelection | null {
    if (isSpectralFilter(input.filterType)) {
        const path = chooseSourceFile(input, 'spectral');
        return path ? { paths: [path], filterType: 'SPEC' } : null;
    }
    const path = chooseSourceFile(input, 'imaging');
    return path ? { paths: [path], filterType: input.filterType || undefined } : null;
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
