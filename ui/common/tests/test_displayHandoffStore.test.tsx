/**
 * @file test_displayHandoffStore.test.tsx
 * @description Tests for the shared hand-off store: latest-wins and queued kinds,
 * single delivery, watchers, and (the race that used to lose requests) a request
 * posted while the receiving display is hidden by React `Activity`, delivered
 * once the display is shown.
 */

import React, { Activity } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { act, render } from '@testing-library/react';
import {
  consumeHandoff,
  observeHandoffs,
  peekHandoffs,
  postHandoff,
  useHandoffConsumer,
} from '../utils/displayHandoffStore';

/** A stand-in display that hands every request of one kind to a spy. */
const Probe: React.FC<{ display: string; kind: string; spy: (payload: unknown) => void }> = ({ display, kind, spy }) => {
  useHandoffConsumer(display, kind, spy);
  return <div data-testid="probe" />;
};

describe('displayHandoffStore', () => {
  /** A plain kind keeps only the newest request. */
  it('keeps only the newest request of a latest-wins kind', () => {
    postHandoff('Planetarium', 'showOnSky', { targetId: 'M 81' });
    postHandoff('Planetarium', 'showOnSky', { targetId: 'M 13' });
    const waiting = peekHandoffs('Planetarium', 'showOnSky');
    expect(waiting).toHaveLength(1);
    expect(waiting[0].payload).toEqual({ targetId: 'M 13' });
  });

  /** A queued kind keeps every request in order, up to its limit. */
  it('keeps a queued kind in order and drops the oldest past the limit', () => {
    for (let index = 1; index <= 5; index += 1) {
      postHandoff('Command Console', 'uiEvent', index, { queue: true, queueLimit: 3 });
    }
    expect(peekHandoffs('Command Console', 'uiEvent').map((item) => item.payload)).toEqual([3, 4, 5]);
  });

  /** Requests for different displays or kinds do not mix. */
  it('keeps displays and kinds apart', () => {
    postHandoff('Planetarium', 'locateStar', 'a');
    postHandoff('Astronomy Manager', 'locateStar', 'b');
    expect(peekHandoffs('Planetarium', 'locateStar')).toHaveLength(1);
    expect(peekHandoffs('Planetarium', 'showOnSky')).toHaveLength(0);
  });

  /** A watcher sees each post, and stops when asked. */
  it('tells watchers about posts until they stop', () => {
    const seen = vi.fn();
    const stop = observeHandoffs('targetSelected', seen);
    postHandoff('Image Processing', 'targetSelected', 'M 13');
    stop();
    postHandoff('Image Processing', 'targetSelected', 'M 81');
    expect(seen).toHaveBeenCalledTimes(1);
    expect(seen.mock.calls[0][0].payload).toBe('M 13');
  });

  /** A consumed request is gone. */
  it('removes a request once it is consumed', () => {
    postHandoff('Planetarium', 'showOnSky', { targetId: 'M 81' });
    consumeHandoff(peekHandoffs('Planetarium', 'showOnSky')[0]);
    expect(peekHandoffs('Planetarium', 'showOnSky')).toHaveLength(0);
  });

  /** A running display gets a request once, as soon as it is posted. */
  it('delivers a request to a visible display exactly once', () => {
    const spy = vi.fn();
    render(<Probe display="Planetarium" kind="showOnSky" spy={spy} />);
    act(() => {
      postHandoff('Planetarium', 'showOnSky', { targetId: 'M 81' });
    });
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith({ targetId: 'M 81' }, expect.anything());
  });

  /** A request posted before the display exists is delivered when it mounts. */
  it('delivers a request posted before the display mounted', () => {
    const spy = vi.fn();
    postHandoff('Planetarium', 'showOnSky', { targetId: 'M 81' });
    render(<Probe display="Planetarium" kind="showOnSky" spy={spy} />);
    expect(spy).toHaveBeenCalledTimes(1);
  });

  /** The race that broke before: a request sent while the display is hidden and paused. */
  it.each([
    ['Astronomy Manager', 'astronomySelectStar', 'HD 151023'],
    ['Image Processing', 'targetSelected', { targetId: 'M 13', starId: 's', enableAstrometry: true }],
    ['Planetarium', 'locateStar', { id: 'Vega' }],
    ['Planetarium', 'showOnSky', { targetId: 'M 81' }],
    ['Observatory Manager', 'showCalibrationRow', { calibrationKey: 'dark', captureSetting: null, rule: 'r' }],
    ['Image Viewer', 'showFrames', { rowIds: [1], targetId: 'M 81', statementText: 't' }],
  ])('delivers a %s %s request posted while the display is paused', (display, kind, payload) => {
    const spy = vi.fn();
    const { rerender } = render(
      <Activity mode="hidden">
        <Probe display={display} kind={kind} spy={spy} />
      </Activity>
    );
    act(() => {
      postHandoff(display, kind, payload);
    });
    expect(spy).not.toHaveBeenCalled();
    rerender(
      <Activity mode="visible">
        <Probe display={display} kind={kind} spy={spy} />
      </Activity>
    );
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy.mock.calls[0][0]).toEqual(payload);
    expect(peekHandoffs(display, kind)).toHaveLength(0);
  });

  /** Queued terminal output: nothing is lost and the order is kept across a pause. */
  it('keeps every queued event, in order, while the display is paused', () => {
    const spy = vi.fn();
    const { rerender } = render(
      <Activity mode="hidden">
        <Probe display="Command Console" kind="uiEvent" spy={spy} />
      </Activity>
    );
    act(() => {
      postHandoff('Command Console', 'uiEvent', 'first', { queue: true });
      postHandoff('Command Console', 'uiEvent', 'second', { queue: true });
      postHandoff('Command Console', 'uiEvent', 'third', { queue: true });
    });
    rerender(
      <Activity mode="visible">
        <Probe display="Command Console" kind="uiEvent" spy={spy} />
      </Activity>
    );
    expect(spy.mock.calls.map((call) => call[0])).toEqual(['first', 'second', 'third']);
  });

  /** An old unsubscribe must not drop watchers that joined the kind after it. */
  it('keeps the watchers of a kind when an earlier unsubscribe runs twice', () => {
    const first = vi.fn();
    const second = vi.fn();
    const stopFirst = observeHandoffs('lateKind', first);
    stopFirst();
    observeHandoffs('lateKind', second);
    // The first watcher's set is gone; running its unsubscribe again must not remove the new set.
    stopFirst();
    postHandoff('Image Processing', 'lateKind', 1);
    expect(second).toHaveBeenCalledTimes(1);
    expect(first).not.toHaveBeenCalled();
  });
});
