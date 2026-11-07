/**
 * @fileoverview Tests that one failed slew gives one error message, not two.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

const callBackendMock = vi.fn();
vi.mock('../services/backendApi', () => ({ callBackend: (...args: unknown[]) => callBackendMock(...args) }));
const emitToastMock = vi.fn();
vi.mock('../utils/emitToast', () => ({ emitToast: (...args: unknown[]) => emitToastMock(...args) }));

import { slewTelescope } from '../services/telescope/motionService';

describe('slewTelescope', () => {
  beforeEach(() => vi.clearAllMocks());

  /** A refusal is shown once by callBackend; the service adds no second message. */
  it('adds no second message when the backend refused the command', async () => {
    callBackendMock.mockRejectedValue(new Error('Command requires MOUNT_CONTROL to be AUTHORITATIVE'));
    expect(await slewTelescope(1, 2)).toBe(false);
    expect(emitToastMock).not.toHaveBeenCalled();
  });

  /** A slew the mount does not accept (no error raised) gets one message. */
  it('shows one message when the mount did not accept the slew', async () => {
    callBackendMock.mockResolvedValue(false);
    expect(await slewTelescope(1, 2)).toBe(false);
    expect(emitToastMock).toHaveBeenCalledTimes(1);
  });

  /** A good slew is quiet. */
  it('is quiet when the slew is accepted', async () => {
    callBackendMock.mockResolvedValue(true);
    expect(await slewTelescope(1, 2)).toBe(true);
    expect(emitToastMock).not.toHaveBeenCalled();
  });
});
