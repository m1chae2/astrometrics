// Lightweight shim for `node:fs` and `node:fs/promises` imports
// This provides minimal no-op/read stubs so bundlers don't fail when
// modules contain conditional node-only imports that are not used in the browser.

export function writeFileSync() {
  // noop in renderer/build context
}

export async function readFile() {
  // return empty Uint8Array to satisfy consumers that expect ArrayBuffer/Buffer-like data
  return new Uint8Array();
}

export default {
  writeFileSync,
  readFile,
};
