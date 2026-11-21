/**
 * @file displayFlags.ts
 * @description Single source of truth for which displays exist and the
 * `[Frontend]` config flag that enables or disables each one, so the mode
 * dropdown (StatusHeader), the mode-change guard (App), and the Settings
 * checkboxes (SystemForm) all agree on the same list.
 */

export interface DisplayDefinition {
  mode: string;
  flag: string;
}

// Order matches how displays are listed in the mode dropdown and in Settings.
export const DISPLAY_DEFINITIONS: DisplayDefinition[] = [
  { mode: 'Image Viewer', flag: 'enable_image_viewer' },
  { mode: 'Image Processing', flag: 'enable_image_processing' },
  { mode: 'Command Console', flag: 'enable_command_console' },
  { mode: 'Astronomy Manager', flag: 'enable_astronomy' },
  { mode: 'Planetarium', flag: 'enable_planetarium' },
  { mode: 'Observatory Manager', flag: 'enable_observatory' },
  { mode: 'Observation Manager', flag: 'enable_observation' },
];

/**
 * Report whether `mode` is enabled in `config`. A display with no matching
 * entry in `DISPLAY_DEFINITIONS` is always enabled. A known display is
 * enabled unless its flag is explicitly set to `'false'`, so it stays
 * available before the config has loaded and for anyone upgrading from a
 * config file that predates the flag.
 */
export const isDisplayEnabled = (
  config: Record<string, Record<string, unknown>>,
  mode: string
): boolean => {
  const definition = DISPLAY_DEFINITIONS.find((d) => d.mode === mode);
  if (!definition) return true;
  return config['Frontend']?.[definition.flag] !== 'false';
};

/** Return every display mode that is currently enabled, in dropdown order. */
export const getEnabledDisplayModes = (config: Record<string, Record<string, unknown>>): string[] =>
  DISPLAY_DEFINITIONS.filter((d) => isDisplayEnabled(config, d.mode)).map((d) => d.mode);

/**
 * Decide which display to show instead of `mode`, or none when `mode` may stay.
 *
 * A turned-off display stays shown for this visit when the person asked for it
 * (for example by clicking a star that opens in the Astronomy Manager). The
 * saved settings are never changed. Otherwise a turned-off display is replaced
 * by the first enabled one.
 *
 * @param config The app settings.
 * @param mode The display being shown.
 * @param requestedMode The display the person last asked to open on purpose, or null.
 * @returns The display to switch to, or null to keep `mode`.
 */
export const fallbackDisplayMode = (
  config: Record<string, Record<string, unknown>>,
  mode: string,
  requestedMode: string | null
): string | null => {
  if (isDisplayEnabled(config, mode) || mode === requestedMode) return null;
  return DISPLAY_DEFINITIONS.find((d) => isDisplayEnabled(config, d.mode))?.mode || 'Image Viewer';
};
