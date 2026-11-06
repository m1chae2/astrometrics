/**
 * @fileoverview Unit tests for backend error handling in callBackend.
 *
 * Verifies that a JSON-RPC error carrying an ErrorInfo record becomes a
 * BackendError that keeps the code, details, retry flag and request id, and
 * that the toast shown for it depends on the error's code.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { callBackend } from '../common/services/backendApi';
import { BackendError, presentBackendError } from '../common/services/backendError';
import * as toastModule from '../common/utils/emitToast';

/** Builds a fetch that answers HTTP 200 with a JSON-RPC error reply. */
function errorReply(data: Record<string, unknown> | undefined, code = -32001, message = 'raw message') {
  return vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    statusText: 'OK',
    json: async () => ({ jsonrpc: '2.0', id: '1', error: { code, message, data } }),
  });
}

describe('callBackend error handling', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  /** The error record from the backend is kept on the thrown error. */
  it('throws a BackendError that keeps the ErrorInfo fields', async () => {
    global.fetch = errorReply({
      code: 'not_found',
      message: "No target 'M 99'.",
      details: { target: 'M 99' },
      retryable: false,
      requestId: 'abc123',
    });
    vi.spyOn(toastModule, 'emitToast').mockImplementation(() => {});

    const failure = await callBackend('target:get' as any, {} as any).catch((e: unknown) => e);

    expect(failure).toBeInstanceOf(BackendError);
    const error = failure as BackendError;
    expect(error.code).toBe('not_found');
    expect(error.message).toBe("No target 'M 99'.");
    expect(error.details).toEqual({ target: 'M 99' });
    expect(error.retryable).toBe(false);
    expect(error.requestId).toBe('abc123');
  });

  /** An error with no data field still becomes a BackendError with the internal code. */
  it('treats an error without ErrorInfo as internal', async () => {
    global.fetch = errorReply(undefined, -32603, 'Internal error');
    vi.spyOn(toastModule, 'emitToast').mockImplementation(() => {});

    const failure = (await callBackend('target:get' as any, {} as any).catch((e: unknown) => e)) as BackendError;

    expect(failure).toBeInstanceOf(BackendError);
    expect(failure.code).toBe('internal');
    expect(failure.message).toBe('Internal error');
  });

  /** An invalid argument is shown as a warning, any other failure as an error. */
  it('shows the toast kind that the error code calls for', async () => {
    const toast = vi.spyOn(toastModule, 'emitToast').mockImplementation(() => {});

    global.fetch = errorReply({ code: 'invalid_argument', message: 'detail must be one of: summary.' });
    await callBackend('target:get' as any, {} as any).catch(() => {});
    expect(toast).toHaveBeenLastCalledWith('detail must be one of: summary.', 'warning', 'API:target:get');

    global.fetch = errorReply({ code: 'hardware', message: 'Mount offline.', retryable: true });
    await callBackend('target:get' as any, {} as any).catch(() => {});
    expect(toast).toHaveBeenLastCalledWith('Mount offline. Trying again may work.', 'error', 'API:target:get');
  });

  /** A silent call shows no toast but still throws. */
  it('does not toast a silent call', async () => {
    const toast = vi.spyOn(toastModule, 'emitToast').mockImplementation(() => {});
    global.fetch = errorReply({ code: 'conflict', message: 'Device in use.' });

    await expect(callBackend('target:get' as any, {} as any, { silent: true })).rejects.toBeInstanceOf(BackendError);

    expect(toast).not.toHaveBeenCalled();
  });
});

describe('presentBackendError', () => {
  /** A missing frame shows only its file name; the full path goes to the console. */
  it('shortens a not-found file path to the file name', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const error = new BackendError({
      code: 'not_found',
      message: 'Frame not found: /home/michael/.local/share/lib/M 13/Apertura 75Q/M_13_Light_028.fits',
    });
    expect(presentBackendError(error).text).toBe("Couldn't open M_13_Light_028.fits");
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  /** Another message with a path keeps its words and loses the folders. */
  it('cuts other file paths down to the file name', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const error = new BackendError({ code: 'storage', message: 'Cannot write /var/data/my stacks/out.fits today.' });
    expect(presentBackendError(error).text).toBe('Cannot write out.fits today.');
    warn.mockRestore();
  });

  /** An internal error shows the backend's sentence, which holds the request id. */
  it('shows the message of an internal error unchanged', () => {
    const error = new BackendError({ code: 'internal', message: 'An internal error occurred. Reference: r1.' });
    expect(presentBackendError(error)).toEqual({
      text: 'An internal error occurred. Reference: r1.',
      kind: 'error',
    });
  });

  /** A background read that fails because a device is unreachable shows no toast; the status chip shows it. */
  it('shows no toast when a background read finds a service unreachable', async () => {
    const toast = vi.spyOn(toastModule, 'emitToast').mockImplementation(() => {});
    global.fetch = errorReply({ code: 'external_service', message: 'Observatory computer unreachable.' });

    await callBackend('plan:current_session' as any, {} as any).catch(() => {});

    expect(toast).not.toHaveBeenCalled();
  });

  /** An action the user asked for still shows its error when a service is unreachable. */
  it('still shows a toast when a user action finds a service unreachable', async () => {
    const toast = vi.spyOn(toastModule, 'emitToast').mockImplementation(() => {});
    global.fetch = errorReply({ code: 'external_service', message: 'Observatory computer unreachable.' });

    await callBackend('telescope:park' as any, {} as any).catch(() => {});

    expect(toast).toHaveBeenCalledTimes(1);
  });
});
