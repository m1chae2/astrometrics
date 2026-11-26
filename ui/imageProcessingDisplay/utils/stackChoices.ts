/**
 * @file stackChoices.ts
 * @description Names the stacks of a target for the stack picker in Image Processing. A target
 * imaged with more than one camera or lens has one stack for each; the backend lists them in the
 * file list (`stacks`), the main stack first. Picking a stack also sets the File Browser's camera
 * and optic filters to that stack's, when the File Browser lists a matching camera and optic.
 */
import type { StackChoice } from '../../common/types/backendTypes';
import type { PopoverSelectOption } from '../../common/components/PopoverSelect';
import { buildOpticOptions, filterFilesByCamera, roundFocalLength, type OpticFrame } from './opticSelection';

/**
 * Writes the name of one stack, such as "ZWO ASI 533MM Pro · 405 mm (main)".
 *
 * @param choice One stack from the file list.
 * @returns The camera, then the focal length when known, then "(main)" for the main stack.
 */
export function stackChoiceLabel(choice: StackChoice): string {
    const camera = choice.camera?.trim() || choice.configurationKey;
    const optic = choice.focalLengthMm != null ? ` · ${Math.round(choice.focalLengthMm)} mm` : '';
    return `${camera}${optic}${choice.isMain ? ' (main)' : ''}`;
}

/**
 * Turns the stacks into picker entries, with the number of frames in each as the detail.
 *
 * @param choices The stacks from the file list.
 * @returns One entry per stack, keyed by its configuration key.
 */
export function stackChoiceOptions(choices: readonly StackChoice[]): PopoverSelectOption[] {
    return choices.map((choice) => ({
        id: choice.configurationKey,
        label: stackChoiceLabel(choice),
        detail: choice.framesStacked ? `${choice.framesStacked} frames` : undefined,
    }));
}

/**
 * Tells whether a path is one of the target's stacks by camera and optic.
 *
 * @param choices The stacks from the file list.
 * @param path The path to check, or null.
 * @returns True when one of the stacks has that path.
 */
export function isStackChoicePath(choices: readonly StackChoice[], path: string | null): boolean {
    return path !== null && choices.some((choice) => choice.stackedImage === path);
}

/** The File Browser filters that match one stack. null means "no match, leave that filter alone". */
export interface StackFilterMatch {
    /** The camera name exactly as the File Browser's camera filter lists it, or null. */
    camera: string | null;
    /** The focal length as the File Browser's optic filter lists it (for example "300"), or null. */
    optic: string | null;
}

/**
 * Makes a camera name easy to compare: lower case, letters and digits only.
 *
 * @param name A camera name, such as "ZWO ASI 533MM Pro".
 * @returns The name with no spaces or marks, such as "zwoasi533mmpro".
 */
function compactCameraName(name: string): string {
    return name.toLowerCase().replace(/[^a-z0-9]/g, '');
}

/**
 * Splits a camera name into lower-case words of letters and digits.
 *
 * @param name A camera name.
 * @returns The words, such as ["nikon", "dslr", "dsc", "d5300"].
 */
function cameraWords(name: string): Set<string> {
    return new Set(name.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean));
}

/**
 * Tells whether every word of one set is also in the other.
 *
 * @param small The words that must all be found.
 * @param large The words to look in.
 * @returns True when each word of `small` is in `large`.
 */
function allWordsIn(small: Set<string>, large: Set<string>): boolean {
    return small.size > 0 && Array.from(small).every((word) => large.has(word));
}

/**
 * Finds the camera in a list that is the same camera as a stack's, even when the two are spelled
 * a little differently ("ZWO ASI533MM Pro" and "ZWO ASI 533MM Pro", or "Nikon D5300" and
 * "Nikon DSLR DSC D5300"). It tries, in order: the same letters and digits, one name inside the
 * other, and then all the words of one name in the other. A step counts only when it finds
 * exactly one camera, so two cameras that both fit give no match.
 *
 * @param stackCamera The camera of the stack.
 * @param cameraNames The camera names the File Browser lists.
 * @returns The matching name from `cameraNames`, or null when none (or more than one) fits.
 */
export function matchCameraName(stackCamera: string, cameraNames: readonly string[]): string | null {
    const wanted = compactCameraName(stackCamera);
    if (!wanted) return null;
    const names = Array.from(new Set(cameraNames.filter((name) => compactCameraName(name))));
    const steps: Array<(name: string) => boolean> = [
        (name) => compactCameraName(name) === wanted,
        (name) => compactCameraName(name).includes(wanted) || wanted.includes(compactCameraName(name)),
        (name) => {
            const stackWords = cameraWords(stackCamera);
            const nameWords = cameraWords(name);
            return allWordsIn(nameWords, stackWords) || allWordsIn(stackWords, nameWords);
        },
    ];
    for (const step of steps) {
        const found = names.filter(step);
        if (found.length === 1) return found[0];
        if (found.length > 1) return null;
    }
    return null;
}

/**
 * Works out the File Browser filters for one stack, so picking a stack can show its frames.
 *
 * @param choice The stack that was picked.
 * @param files Every frame of the target, with its camera and focal length.
 * @returns The camera filter and optic filter values to use. Each is null when the File Browser
 *   has no matching choice, so that filter is left as it is.
 */
export function stackFilterMatch(choice: StackChoice, files: readonly OpticFrame[]): StackFilterMatch {
    const cameraNames = files.map((file) => file.camera ?? '');
    const camera = choice.camera ? matchCameraName(choice.camera, cameraNames) : null;
    // The optic choices are for the camera filter that will be in force.
    const framesOfCamera = camera ? filterFilesByCamera([...files], camera) : [...files];
    const focalLength = roundFocalLength(choice.focalLengthMm);
    const opticIds = buildOpticOptions(framesOfCamera).map((option) => option.id);
    const optic = focalLength !== null && opticIds.includes(String(focalLength)) ? String(focalLength) : null;
    return { camera, optic };
}
