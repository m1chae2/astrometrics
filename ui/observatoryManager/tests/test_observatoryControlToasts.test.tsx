/**
 * @fileoverview Tests that the Observatory Manager's command handlers send no "echo" toasts for the
 * user's own actions (parking, slewing, a manual move), and still show a failure.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';

const { emitToastMock, motion, guiding } = vi.hoisted(() => ({
  emitToastMock: vi.fn(),
  motion: {
    parkTelescope: vi.fn(async () => undefined),
    unparkTelescope: vi.fn(async () => undefined),
    slewToTarget: vi.fn(async () => true),
    abortTelescopeMotion: vi.fn(async () => undefined),
    setTelescopeTracking: vi.fn(async () => undefined),
    moveTelescope: vi.fn(async () => undefined),
    setTelescopeSlewRate: vi.fn(async () => undefined),
  },
  guiding: {
    startGuiding: vi.fn(async () => true),
    stopGuiding: vi.fn(async () => true),
    captureGuideFrame: vi.fn(async () => true),
  },
}));

vi.mock('../../common/utils/emitToast', () => ({ emitToast: (...args: unknown[]) => emitToastMock(...args) }));
vi.mock('../../common/services/telescope/motionService', () => motion);
vi.mock('../../common/services/telescope/guidingService', () => guiding);
vi.mock('../../common/services/alignmentApi', () => ({ startAlignment: vi.fn(async () => undefined), stopAlignment: vi.fn(async () => undefined) }));
vi.mock('../../common/services/targetService', () => ({ createTarget: vi.fn(async () => undefined), fetchTargetObject: vi.fn(async (id: string) => (id === 'M 31' ? { ra: '0h 42m 44s', dec: '+41d 16m 09s' } : { id })) }));

import { useObservatoryControl } from '../hooks/useObservatoryControl';

describe('useObservatoryControl toasts', () => {
  beforeEach(() => {
    emitToastMock.mockReset();
  });

  /** Every command runs and none of them toasts. */
  it('sends no toast for commands that work', async () => {
    const { result } = renderHook(() => useObservatoryControl(vi.fn()));
    const { handlers } = result.current;
    await act(async () => {
      await handlers.onPark();
      await handlers.onUnpark();
      await handlers.onSlewToTarget('M 31');
      await handlers.onStop();
      await handlers.onStartMove('N');
      await handlers.onStopMove('N');
      await handlers.onSetSlewRate(1);
      await handlers.onSetTracking(true);
      await handlers.onGuideCapture();
      await handlers.onStartAlignment('M 31');
      await handlers.onStopAlignment();
    });
    expect(motion.parkTelescope).toHaveBeenCalled();
    expect(motion.moveTelescope).toHaveBeenCalledWith('N', true);
    expect(emitToastMock).not.toHaveBeenCalled();
  });

  /** A target the library has no coordinates for is still reported, as an error. */
  it('still shows an error when alignment has no coordinates to use', async () => {
    const { result } = renderHook(() => useObservatoryControl(vi.fn()));
    let started = true;
    await act(async () => { started = await result.current.handlers.onStartAlignment('Nowhere'); });
    expect(started).toBe(false);
    expect(emitToastMock).toHaveBeenCalledWith(expect.stringContaining('has no coordinates'), 'error');
  });
});
