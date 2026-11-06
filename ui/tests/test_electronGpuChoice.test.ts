/**
 * @fileoverview Unit tests for electron/gpu_choice.js (which graphics card the app starts on) and
 * electron/gpu_startup_check.js (the check of what Electron really got). They use fake power supply and driver
 * folders in a temporary directory, so they do not depend on the computer they run on.
 */

import fs from 'fs';
import os from 'os';
import path from 'path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  appLaunchPlan,
  classifyRenderer,
  isAcPowerOnline,
  readGpuSettingFromConfig,
  resolveGpuChoice,
  shellPlanLines,
} from '../../electron/gpu_choice.js';
import { checkGpuAtStartup } from '../../electron/gpu_startup_check.js';

const temporaryFolders: string[] = [];

/** Makes a fake power supply folder. */
function makePowerSupply(supplies: Record<string, { type: string; online?: string }>): string {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'astrometrics-power-'));
  temporaryFolders.push(root);
  for (const [name, values] of Object.entries(supplies)) {
    fs.mkdirSync(path.join(root, name));
    fs.writeFileSync(path.join(root, name, 'type'), `${values.type}\n`);
    if (values.online !== undefined) fs.writeFileSync(path.join(root, name, 'online'), `${values.online}\n`);
  }
  return root;
}

/** A fake file system where the NVIDIA driver files are, or are not, present. */
function fakeFileSystem(nvidiaPresent: boolean, configText: string | null = null) {
  return {
    ...fs,
    existsSync: (file: string) => (file === '/proc/driver/nvidia/version' ? nvidiaPresent : fs.existsSync(file)),
    readdirSync: (folder: string, ...rest: unknown[]) =>
      folder.endsWith('glvnd/egl_vendor.d') ? (nvidiaPresent ? ['10_nvidia.json', '50_mesa.json'] : ['50_mesa.json']) : (fs.readdirSync as any)(folder, ...rest),
    readFileSync: (file: string, ...rest: unknown[]) => {
      if (String(file).endsWith('astrometrics.config.toml')) {
        if (configText === null) throw new Error('no file');
        return configText;
      }
      return (fs.readFileSync as any)(file, ...rest);
    },
  } as unknown as typeof fs;
}

afterEach(() => {
  for (const folder of temporaryFolders.splice(0)) fs.rmSync(folder, { recursive: true, force: true });
});

describe('isAcPowerOnline', () => {
  it('is true when the Mains adapter is online', () => {
    const root = makePowerSupply({ AC: { type: 'Mains', online: '1' }, BAT0: { type: 'Battery' } });
    expect(isAcPowerOnline(root)).toBe(true);
  });
  it('is false when the Mains adapter is offline', () => {
    const root = makePowerSupply({ AC: { type: 'Mains', online: '0' }, BAT0: { type: 'Battery' } });
    expect(isAcPowerOnline(root)).toBe(false);
  });
  it('ignores a USB supply that is online when the Mains adapter is offline', () => {
    const root = makePowerSupply({ AC: { type: 'Mains', online: '0' }, usb: { type: 'USB', online: '1' }, BAT0: { type: 'Battery' } });
    expect(isAcPowerOnline(root)).toBe(false);
  });
  it('counts a computer with no battery and no adapter as on AC', () => {
    expect(isAcPowerOnline(makePowerSupply({}))).toBe(true);
  });
});

describe('readGpuSettingFromConfig', () => {
  it('reads the gpu key from [Frontend] only', () => {
    const text = '[Other]\ngpu = "intel"\n\n[Frontend]\nenable_x = "true"\ngpu = "nvidia" # comment\n';
    expect(readGpuSettingFromConfig({ ASTROMETRICS_CONFIG_PATH: '/x/astrometrics.config.toml' }, fakeFileSystem(true, text))).toBe('nvidia');
  });
  it('returns null when the file or key is missing', () => {
    expect(readGpuSettingFromConfig({ ASTROMETRICS_CONFIG_PATH: '/x/astrometrics.config.toml' }, fakeFileSystem(true, null))).toBeNull();
    expect(readGpuSettingFromConfig({ ASTROMETRICS_CONFIG_PATH: '/x/astrometrics.config.toml' }, fakeFileSystem(true, '[Frontend]\nenable_x = "1"\n'))).toBeNull();
  });
});

describe('resolveGpuChoice', () => {
  const onAc = () => makePowerSupply({ AC: { type: 'Mains', online: '1' } });
  const onBattery = () => makePowerSupply({ AC: { type: 'Mains', online: '0' }, BAT0: { type: 'Battery' } });
  const environment = { ASTROMETRICS_CONFIG_PATH: '/x/astrometrics.config.toml' };

  it('auto picks NVIDIA on AC with the driver present', () => {
    expect(resolveGpuChoice({ environment, fileSystem: fakeFileSystem(true), powerSupplyRoot: onAc() }).choice).toBe('nvidia');
  });
  it('auto picks Intel on battery', () => {
    expect(resolveGpuChoice({ environment, fileSystem: fakeFileSystem(true), powerSupplyRoot: onBattery() }).choice).toBe('intel');
  });
  it('auto picks Intel when there is no NVIDIA driver, even on AC', () => {
    expect(resolveGpuChoice({ environment, fileSystem: fakeFileSystem(false), powerSupplyRoot: onAc() }).choice).toBe('intel');
  });
  it('the config key overrides auto', () => {
    const fileSystem = fakeFileSystem(true, '[Frontend]\ngpu = "intel"\n');
    expect(resolveGpuChoice({ environment, fileSystem, powerSupplyRoot: onAc() }).choice).toBe('intel');
  });
  it('the environment variable beats the config key', () => {
    const fileSystem = fakeFileSystem(true, '[Frontend]\ngpu = "intel"\n');
    const result = resolveGpuChoice({ environment: { ...environment, ASTROMETRICS_GPU: 'nvidia' }, fileSystem, powerSupplyRoot: onBattery() });
    expect(result.choice).toBe('nvidia');
  });
  it('nvidia without the driver falls back to Intel', () => {
    const result = resolveGpuChoice({ environment: { ...environment, ASTROMETRICS_GPU: 'nvidia' }, fileSystem: fakeFileSystem(false), powerSupplyRoot: onAc() });
    expect(result.choice).toBe('intel');
  });
  it('an invalid value is ignored and reads as auto', () => {
    const result = resolveGpuChoice({ environment: { ...environment, ASTROMETRICS_GPU: 'banana' }, fileSystem: fakeFileSystem(true), powerSupplyRoot: onAc() });
    expect(result.setting).toBe('auto');
    expect(result.choice).toBe('nvidia');
  });
});

describe('launch plans', () => {
  it('NVIDIA sets the offload variables and the hardware switches', () => {
    const plan = appLaunchPlan('nvidia', {});
    expect(plan.environment.__NV_PRIME_RENDER_OFFLOAD).toBe('1');
    expect(plan.switches).toContain('--use-angle=gl');
  });
  it('Intel removes the offload variables and adds no switches', () => {
    const plan = appLaunchPlan('intel', { __NV_PRIME_RENDER_OFFLOAD: '1', KEEP: 'yes' });
    expect(plan.environment).toEqual({ KEEP: 'yes' });
    expect(plan.switches).toEqual([]);
  });
  it('the shell lines export for NVIDIA and unset for Intel', () => {
    expect(shellPlanLines({ choice: 'nvidia', reason: 'r' })).toContain("export __NV_PRIME_RENDER_OFFLOAD='1'");
    expect(shellPlanLines({ choice: 'intel', reason: 'r' })).toContain('unset __NV_PRIME_RENDER_OFFLOAD');
  });
  it('classifies renderer strings', () => {
    expect(classifyRenderer('ANGLE (NVIDIA Corporation, NVIDIA GeForce GTX 1650)')).toBe('nvidia');
    expect(classifyRenderer('Mesa Intel(R) UHD Graphics 630')).toBe('intel');
    expect(classifyRenderer('SwiftShader')).toBe('software');
  });
});

describe('checkGpuAtStartup', () => {
  /** A fake Electron app that reports the given renderer. */
  function fakeApp(glRenderer: string) {
    return {
      getGPUInfo: vi.fn(async () => ({ auxAttributes: { glRenderer } })),
      getGPUFeatureStatus: vi.fn(() => ({})),
      relaunch: vi.fn(),
      quit: vi.fn(),
      exit: vi.fn(),
      on: vi.fn(),
    };
  }
  const logger = () => ({ info: vi.fn(), warn: vi.fn() });

  it('logs and does not restart when the requested card is used', async () => {
    const app = fakeApp('ANGLE (NVIDIA Corporation, NVIDIA GeForce GTX 1650)');
    const log = logger();
    const result = await checkGpuAtStartup(app as never, { environment: { ASTROMETRICS_GPU_CHOICE: 'nvidia' }, argv: ['electron'], logger: log });
    expect(result.kind).toBe('nvidia');
    expect(app.relaunch).not.toHaveBeenCalled();
    expect(log.warn).not.toHaveBeenCalled();
  });
  it('warns and restarts once on Intel when NVIDIA was asked for but not used', async () => {
    const app = fakeApp('Mesa Intel(R) UHD Graphics 630');
    const log = logger();
    const environment: Record<string, string> = { ASTROMETRICS_GPU_CHOICE: 'nvidia', __NV_PRIME_RENDER_OFFLOAD: '1' };
    const result = await checkGpuAtStartup(app as never, { environment, argv: ['electron', '.', '--use-gl=angle', '--use-angle=gl', '--keep'], logger: log });
    expect(result.relaunched).toBe(true);
    expect(app.relaunch).toHaveBeenCalledWith({ args: ['.', '--keep'] });
    expect(environment.__NV_PRIME_RENDER_OFFLOAD).toBeUndefined();
    expect(environment.ASTROMETRICS_GPU_FALLBACK).toBe('1');
    expect(log.warn).toHaveBeenCalled();
  });
  it('does not restart a second time', async () => {
    const app = fakeApp('SwiftShader');
    const environment = { ASTROMETRICS_GPU_CHOICE: 'nvidia', ASTROMETRICS_GPU_FALLBACK: '1' };
    const result = await checkGpuAtStartup(app as never, { environment, argv: ['electron'], logger: logger() });
    expect(result.relaunched).toBe(false);
    expect(app.relaunch).not.toHaveBeenCalled();
  });
  it('only logs when no card was requested', async () => {
    const app = fakeApp('Mesa Intel(R) UHD Graphics 630');
    const log = logger();
    await checkGpuAtStartup(app as never, { environment: {}, argv: ['electron'], logger: log });
    expect(log.warn).not.toHaveBeenCalled();
    expect(app.relaunch).not.toHaveBeenCalled();
  });
});
