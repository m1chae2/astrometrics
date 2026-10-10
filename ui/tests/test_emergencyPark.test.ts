/**
 * @fileoverview Unit tests for the emergency park command.
 *
 * Verifies that the command goes through the `telescope:park` RPC method
 * and that a failure is shown to the user instead of being ignored.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

const callBackend = vi.fn();
const emitToast = vi.fn();

vi.mock('../common/services/backendApi', () => ({ callBackend }));
vi.mock('../common/utils/emitToast', () => ({ emitToast }));

describe('emergencyParkMount', () => {
  beforeEach(() => {
    callBackend.mockReset();
    emitToast.mockReset();
  });

  /** The command uses the RPC method the backend registers. */
  it('calls the telescope:park RPC method', async () => {
    callBackend.mockResolvedValue(true);
    const { emergencyParkMount } = await import('../common/services/telescope/emergencyPark');

    await expect(emergencyParkMount()).resolves.toBe(true);

    expect(callBackend).toHaveBeenCalledWith('telescope:park', {});
    expect(emitToast).not.toHaveBeenCalled();
  });

  /** A backend that reports failure produces an error toast. */
  it('shows an error toast when the backend reports failure', async () => {
    callBackend.mockResolvedValue(false);
    const { emergencyParkMount } = await import('../common/services/telescope/emergencyPark');

    await expect(emergencyParkMount()).resolves.toBe(false);

    expect(emitToast).toHaveBeenCalledWith(expect.stringContaining('Emergency park failed'), 'error', 'EmergencyPark');
  });

  /** A request that throws also produces an error toast. */
  it('shows an error toast when the request throws', async () => {
    callBackend.mockRejectedValue(new Error('network down'));
    const { emergencyParkMount } = await import('../common/services/telescope/emergencyPark');

    await expect(emergencyParkMount()).resolves.toBe(false);

    expect(emitToast).toHaveBeenCalledTimes(1);
  });
});
