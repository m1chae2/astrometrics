/**
 * @fileoverview Decodes a `data:` URL into a Blob without calling `fetch`.
 *
 * The app's Content Security Policy (see `electron/main.js`) does not allow
 * `fetch` to read `data:` URLs, so a picture the backend sends as a data URL
 * is decoded here instead.
 */

/**
 * Turns a `data:` URL into a Blob.
 *
 * @param {string} dataUrl - A URL such as `data:image/png;base64,AAAA`.
 * @return {Blob} The decoded bytes, typed with the URL's media type
 *     (`application/octet-stream` when the URL names none).
 * @throws {Error} If the text is not a `data:` URL.
 */
export function dataUrlToBlob(dataUrl: string): Blob {
    const match = /^data:([^,]*?)(;base64)?,(.*)$/s.exec(dataUrl);
    if (!match) {
        throw new Error('Not a data URL.');
    }
    const [, mediaType, base64Marker, payload] = match;
    const type = mediaType || 'application/octet-stream';
    if (!base64Marker) {
        return new Blob([decodeURIComponent(payload)], { type });
    }
    const binary = atob(payload);
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index++) {
        bytes[index] = binary.charCodeAt(index);
    }
    return new Blob([bytes], { type });
}
