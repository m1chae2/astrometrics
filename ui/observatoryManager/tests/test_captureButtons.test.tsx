/**
 * @file test_captureButtons.test.tsx
 * @description Tests of the Start Capture and Stop Capture buttons on the Mount tab: they render, their
 * enabled states follow the capture state, the telescope target and the command gate, they call the
 * commands, and the command handlers use the capture service with its fixed settings.
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const { capture, emitToastMock, callBackendMock } = vi.hoisted(() => ({
  capture: {
    startOpenEndedCapture: vi.fn(async (_targetId: string): Promise<string | null> => 'job-1'),
    stopCapture: vi.fn(async () => true),
  },
  emitToastMock: vi.fn(),
  callBackendMock: vi.fn(),
}));

vi.mock('../../common/services/imaging/captureService', () => capture);
vi.mock('../../common/utils/emitToast', () => ({ emitToast: (...args: unknown[]) => emitToastMock(...args) }));
vi.mock('../../common/services/telescope/motionService', () => ({
  parkTelescope: vi.fn(), unparkTelescope: vi.fn(), slewTelescope: vi.fn(), abortTelescopeMotion: vi.fn(),
  setTelescopeTracking: vi.fn(), moveTelescope: vi.fn(), setTelescopeSlewRate: vi.fn(),
}));
vi.mock('../../common/services/telescope/guidingService', () => ({ captureGuideFrame: vi.fn() }));
vi.mock('../../common/services/alignmentApi', () => ({ startAlignment: vi.fn(), stopAlignment: vi.fn() }));
vi.mock('../../common/services/targetService', () => ({ createTarget: vi.fn() }));
vi.mock('../../common/services/backendApi', () => ({ callBackend: (...args: unknown[]) => callBackendMock(...args) }));

import { TelescopeControl } from '../panels/TelescopeControl';
import { useObservatoryControl } from '../hooks/useObservatoryControl';

/** Renders the Mount tab buttons with the capture props under test. */
const renderControl = (props: Partial<React.ComponentProps<typeof TelescopeControl>> = {}) =>
  render(
    <TelescopeControl
      isTracking
      isParked={false}
      onSetTracking={vi.fn()}
      onPark={vi.fn()}
      onUnpark={vi.fn()}
      {...props}
    />,
  );

describe('Start Capture and Stop Capture buttons', () => {
  beforeEach(() => vi.clearAllMocks());

  it('renders both buttons', () => {
    renderControl();
    expect(screen.getByRole('button', { name: 'Start Capture' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Stop Capture' })).toBeInTheDocument();
  });

  it('enables Start and disables Stop while no capture runs', () => {
    renderControl();
    expect(screen.getByRole('button', { name: 'Start Capture' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Stop Capture' })).toBeDisabled();
  });

  it('disables Start and enables Stop while a capture runs', () => {
    renderControl({ isCapturing: true });
    expect(screen.getByRole('button', { name: 'Start Capture' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Stop Capture' })).toBeEnabled();
  });

  it('greys Start with an explanation when the telescope points at no target', () => {
    renderControl({ startCaptureBlockedReason: 'The telescope is not pointing at a target.' });
    const start = screen.getByRole('button', { name: 'Start Capture' });
    expect(start).toBeDisabled();
    expect(start).toHaveAttribute('title', 'The telescope is not pointing at a target.');
  });

  it('greys both buttons when the app may not command the camera', () => {
    renderControl({ isCapturing: true, captureDisabled: true });
    expect(screen.getByRole('button', { name: 'Start Capture' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Stop Capture' })).toBeDisabled();
  });

  it('calls the start and stop commands', async () => {
    const onStartCapture = vi.fn(async () => true);
    const onStopCapture = vi.fn(async () => true);
    const { rerender } = renderControl({ onStartCapture, onStopCapture });
    await userEvent.click(screen.getByRole('button', { name: 'Start Capture' }));
    expect(onStartCapture).toHaveBeenCalledTimes(1);

    rerender(
      <TelescopeControl
        isTracking isParked={false} onSetTracking={vi.fn()} onPark={vi.fn()} onUnpark={vi.fn()}
        isCapturing onStartCapture={onStartCapture} onStopCapture={onStopCapture}
      />,
    );
    await userEvent.click(screen.getByRole('button', { name: 'Stop Capture' }));
    expect(onStopCapture).toHaveBeenCalledTimes(1);
  });

  it('stops showing pending when the start did not happen', async () => {
    renderControl({ onStartCapture: vi.fn(async () => false) });
    const start = screen.getByRole('button', { name: 'Start Capture' });
    await userEvent.click(start);
    expect(start).not.toHaveAttribute('aria-busy', 'true');
  });
});

describe('useObservatoryControl capture handlers', () => {
  beforeEach(() => vi.clearAllMocks());

  it('starts the capture on the target the telescope points at', async () => {
    const { result } = renderHook(() => useObservatoryControl(vi.fn()));
    let started = false;
    await act(async () => { started = await result.current.handlers.onStartCapture('M 81'); });
    expect(capture.startOpenEndedCapture).toHaveBeenCalledWith('M 81');
    expect(started).toBe(true);
  });

  it('shows an error and starts nothing when the telescope points at no target', async () => {
    const { result } = renderHook(() => useObservatoryControl(vi.fn()));
    let started = true;
    await act(async () => { started = await result.current.handlers.onStartCapture(undefined); });
    expect(started).toBe(false);
    expect(capture.startOpenEndedCapture).not.toHaveBeenCalled();
    expect(emitToastMock).toHaveBeenCalledWith('The telescope is not pointing at a target', 'error');
  });

  it('reports a capture that did not start as not started', async () => {
    capture.startOpenEndedCapture.mockResolvedValueOnce(null);
    const { result } = renderHook(() => useObservatoryControl(vi.fn()));
    let started = true;
    await act(async () => { started = await result.current.handlers.onStartCapture('M 81'); });
    expect(started).toBe(false);
  });

  it('stops the capture through the capture service', async () => {
    const { result } = renderHook(() => useObservatoryControl(vi.fn()));
    await act(async () => { await result.current.handlers.onStopCapture(); });
    expect(capture.stopCapture).toHaveBeenCalledTimes(1);
  });
});
