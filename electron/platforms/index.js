/**
 * @fileoverview Platform adapter factory for Astrometrics Electron main process.
 * Detects the host platform and returns the appropriate Platform instance.
 */

import process from 'process';
import { BasePlatform } from './base.js';
import { WindowsPlatform } from './windows.js';
import { LinuxPlatform } from './linux.js';
import { MacOSPlatform } from './macos.js';

let platformInstance = null;

/**
 * Returns the singleton platform adapter for the current host OS.
 *
 * @param {string} [overridePlatform] - Optional platform override for testing.
 * @returns {BasePlatform}
 */
export function getPlatform(overridePlatform) {
  if (overridePlatform) {
    switch (overridePlatform) {
      case 'win32':
        return new WindowsPlatform();
      case 'linux':
        return new LinuxPlatform();
      case 'darwin':
        return new MacOSPlatform();
      default:
        return new BasePlatform();
    }
  }

  if (!platformInstance) {
    switch (process.platform) {
      case 'win32':
        platformInstance = new WindowsPlatform();
        break;
      case 'linux':
        platformInstance = new LinuxPlatform();
        break;
      case 'darwin':
        platformInstance = new MacOSPlatform();
        break;
      default:
        platformInstance = new BasePlatform();
        break;
    }
  }

  return platformInstance;
}
