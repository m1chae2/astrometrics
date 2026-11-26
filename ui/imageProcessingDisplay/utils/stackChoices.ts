/**
 * @file stackChoices.ts
 * @description Names the stacks of a target for the stack picker in Image Processing. A target
 * imaged with more than one camera or lens has one stack for each; the backend lists them in the
 * file list (`stacks`), the main stack first.
 */
import type { StackChoice } from '../../common/types/backendTypes';
import type { PopoverSelectOption } from '../../common/components/PopoverSelect';

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
