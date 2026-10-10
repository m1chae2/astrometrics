/**
 * @fileoverview Tests for decoding data URLs without fetch.
 */
import { describe, expect, it } from 'vitest';
import { dataUrlToBlob } from '../common/utils/dataUrl';

/** Reads a blob's bytes with FileReader, which the test DOM provides. */
const readBytes = (blob: Blob): Promise<Uint8Array> =>
    new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
        reader.onerror = () => reject(reader.error);
        reader.readAsArrayBuffer(blob);
    });

describe('dataUrlToBlob', () => {
    it('decodes a base64 PNG header into bytes with the right type', async () => {
        const blob = dataUrlToBlob('data:image/png;base64,iVBORw0KGgo=');
        expect(blob.type).toBe('image/png');
        const bytes = await readBytes(blob);
        expect(Array.from(bytes.slice(0, 4))).toEqual([0x89, 0x50, 0x4e, 0x47]);
    });

    it('decodes a plain-text data URL', async () => {
        const blob = dataUrlToBlob('data:text/plain,hello%20sky');
        expect(new TextDecoder().decode(await readBytes(blob))).toBe('hello sky');
    });

    it('rejects text that is not a data URL', () => {
        expect(() => dataUrlToBlob('http://example.com/a.png')).toThrow('Not a data URL.');
    });
});
