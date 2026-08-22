/**
 * @fileoverview Main entry point for telescope services.
 * Re-exports from specialized sub-services to maintain backward compatibility
 * while strictly adhering to the file size limit and separation of concerns.
 */

export * from './telescope/statusService';
export * from './telescope/motionService';
export * from './telescope/deviceService';
export * from './telescope/guidingService';
