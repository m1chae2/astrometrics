/**
 * @fileoverview Checks that the test backend's settings and folders stay inside its throwaway folder.
 *
 * A settings file with no wayfinding entry once sent the backend to the user's real
 * `wayfinding.db`, where the suite's setup call changed the mount control setting.
 */

import fs from 'fs';
import os from 'os';
import path from 'path';
import { afterEach, describe, expect, it } from 'vitest';
import { testBackendEnvironment, writeSandboxConfig } from '../globalSetup';

const sandboxes: string[] = [];

/**
 * Makes an empty throwaway folder that is removed after the test.
 *
 * @return {string} The folder's path.
 */
function makeSandbox(): string {
    const sandbox = fs.mkdtempSync(path.join(os.tmpdir(), 'astrometrics-sandbox-test-'));
    sandboxes.push(sandbox);
    return sandbox;
}

afterEach(() => {
    for (const sandbox of sandboxes.splice(0)) fs.rmSync(sandbox, { recursive: true, force: true });
});

describe('test backend sandbox', () => {
    it('names the image library and the wayfinding library inside the folder', () => {
        /** Every library path in the settings file must lie inside the sandbox. */
        const sandbox = makeSandbox();
        const text = fs.readFileSync(writeSandboxConfig(sandbox), 'utf8');

        expect(text).toContain('["Wayfinding Library"]');
        const paths = [...text.matchAll(/^\w*path = "(.*)"$/gm)].map((match) => match[1]);
        expect(paths).toHaveLength(3);
        for (const stored of paths) {
            expect(stored.startsWith(sandbox + path.sep)).toBe(true);
            expect(fs.existsSync(stored)).toBe(true);
        }
    });

    it('points the data, cache and state folders inside the folder, whatever the parent set', () => {
        /** The real folders in the parent environment must not reach the backend. */
        const sandbox = makeSandbox();
        const configPath = writeSandboxConfig(sandbox);
        const environment = testBackendEnvironment(
            {
                HOME: '/home/someone',
                ASTROMETRICS_CONFIG: '/real/config.toml',
                ASTROMETRICS_CONFIG_PATH: '/real/config.toml',
                XDG_DATA_HOME: '/real/data',
                XDG_CACHE_HOME: '/real/cache',
            },
            configPath,
            5123,
        );

        for (const name of ['XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME']) {
            expect(environment[name]?.startsWith(sandbox + path.sep)).toBe(true);
        }
        expect(environment.ASTROMETRICS_CONFIG).toBe(configPath);
        expect(environment.ASTROMETRICS_CONFIG_PATH).toBe(configPath);
        expect(environment.ASTROMETRICS_PORT).toBe('5123');
    });
});
