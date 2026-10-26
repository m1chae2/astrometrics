/**
 * @file useCommandAvailability.ts
 * @description Re-exports the shared command-availability hook, which now lives in
 * `ui/common/hooks/` so every display that commands a device uses the same answer.
 */

export * from '../../common/hooks/useCommandAvailability';
