/**
 * @file pixelQuery.ts
 * @description Shared steps for finding which image pixel lies under a point on the screen, and for
 * asking the FITS worker what that pixel holds. The pixel readout and the star profile click both use
 * these steps, so a click and the readout always name the same pixel.
 */
import type { ParsedFitsData } from '../hooks/useFitsLoader';
import { screenToImage } from './transformUtils';
import { turnedSize } from '../displayTurn';

/** Source of ids that tie each pixel question to its answer. Shared by every reader on the page. */
let nextPixelRequestId = 1;

/**
 * Hands out an id for one question to the FITS worker.
 *
 * @returns An id no other question on the page has used.
 */
export function newPixelRequestId(): number {
  return nextPixelRequestId++;
}

/** What the worker found at one pixel. */
export interface PixelReading {
  /** FITS column, counted from 1. */
  x: number;
  /** FITS row, counted from 1. */
  y: number;
  /** One value per channel: one for gray, three (red, green, blue) for color. */
  values: number[];
}

/** One cell of the picture as drawn, named the way the worker names it. */
export interface FileCell {
  /** Column of the drawn picture, counted from 0 at the left. */
  column: number;
  /** Row of the drawn picture, counted from 0 at the top. */
  rowFromTop: number;
}

/**
 * Finds the cell of the drawn picture under a point of the viewer. The worker turns the cell into the
 * file's own pixel (see `askWorkerForPixel`).
 *
 * The drawn picture may be smaller than the file, so the point is scaled back to file pixels. When the
 * picture is shown turned by a quarter turn, its width and height are the file's swapped.
 *
 * @param pointerX Horizontal position inside the viewer, in screen pixels.
 * @param pointerY Vertical position inside the viewer, in screen pixels.
 * @param image The decoded image.
 * @param drawnSize Size of the drawn picture, in the units the pan and zoom move it by.
 * @param zoom Zoom of the image on screen.
 * @param panX Offset of the image's left edge from the viewer's left edge, in screen pixels.
 * @param panY Offset of the image's top edge from the viewer's top edge, in screen pixels.
 * @param turn How far the picture is shown turned, clockwise in degrees (default 0).
 * @returns The cell, or null when the point is off the image or the view is not ready.
 */
export function pointerToFileCell(
  pointerX: number,
  pointerY: number,
  image: Pick<ParsedFitsData, 'w' | 'h'>,
  drawnSize: { w: number; h: number },
  zoom: number,
  panX: number,
  panY: number,
  turn: number = 0,
): FileCell | null {
  if (drawnSize.w <= 0 || drawnSize.h <= 0 || zoom <= 0) return null;
  const drawnPoint = screenToImage(pointerX, pointerY, panX, panY, zoom);
  if (drawnPoint.x < 0 || drawnPoint.y < 0 || drawnPoint.x >= drawnSize.w || drawnPoint.y >= drawnSize.h) return null;
  const shown = turnedSize(image.w, image.h, turn);
  return {
    column: Math.min(shown.w - 1, Math.floor((drawnPoint.x / drawnSize.w) * shown.w)),
    rowFromTop: Math.min(shown.h - 1, Math.floor((drawnPoint.y / drawnSize.h) * shown.h)),
  };
}

/**
 * Asks the worker for one pixel and waits for its answer.
 *
 * @param worker The FITS worker that holds the decoded pixels.
 * @param imageId Id the worker filed the image under.
 * @param cell The cell of the drawn picture to read.
 * @returns The reading (the file's own pixel position and values), or null when the worker has no such pixel.
 */
export function askWorkerForPixel(worker: Worker, imageId: number, cell: FileCell): Promise<PixelReading | null> {
  return new Promise((resolve) => {
    const requestId = newPixelRequestId();
    const onMessage = (event: MessageEvent) => {
      if (event.data?.cmd !== 'pixelComplete' || event.data.requestId !== requestId) return;
      worker.removeEventListener('message', onMessage);
      resolve((event.data.result as PixelReading | null) ?? null);
    };
    worker.addEventListener('message', onMessage);
    worker.postMessage({ cmd: 'pixel', requestId, imageId, column: cell.column, rowFromTop: cell.rowFromTop });
  });
}
