/**
 * @file bitmapGuard.ts
 * @description Safe drawing of an ImageBitmap. A bitmap that was closed cannot be
 * drawn: the browser throws an "image source is detached" error. These helpers
 * check for that, so a late draw is skipped instead of crashing the app.
 */

/** True once the "closed bitmap" warning has been printed, so the console is not flooded. */
let warnedAboutClosedBitmap = false;

/**
 * Says whether a bitmap can still be drawn.
 *
 * A closed bitmap reports a width and height of zero.
 *
 * @param bitmap The bitmap to test.
 * @returns True when the bitmap is open.
 */
export const isBitmapUsable = (bitmap: ImageBitmap): boolean => bitmap.width > 0 && bitmap.height > 0;

/**
 * Draws a bitmap at the top-left of a canvas, without ever throwing.
 *
 * @param ctx The canvas to draw on.
 * @param bitmap The bitmap to draw.
 * @returns True when the bitmap was drawn; false when it was closed, so the caller should treat the frame as not drawn.
 */
export const drawBitmapSafely = (ctx: CanvasRenderingContext2D, bitmap: ImageBitmap): boolean => {
    if (!isBitmapUsable(bitmap)) {
        warnOnce('A closed image was skipped; waiting for a usable one.');
        return false;
    }
    try {
        ctx.drawImage(bitmap, 0, 0);
        return true;
    } catch (error) {
        warnOnce(`Drawing an image failed and was skipped: ${error instanceof Error ? error.message : String(error)}`);
        return false;
    }
};

/**
 * Prints a warning the first time only.
 *
 * @param message The text to print.
 */
const warnOnce = (message: string): void => {
    if (warnedAboutClosedBitmap) return;
    warnedAboutClosedBitmap = true;
    console.warn(message);
};

/**
 * Lets tests see the warning again.
 */
export const resetBitmapGuardWarning = (): void => {
    warnedAboutClosedBitmap = false;
};
