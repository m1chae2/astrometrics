/**
 * @fileoverview The marker a test file contains when it needs the shared test backend.
 * Kept in its own file so both the global setup and the per-file setup agree on it.
 */

/** Put this text in a test file (for example in a comment) to get the shared test backend. */
export const TEST_BACKEND_MARKER = '@requires-test-backend';

/**
 * The environment variable that turns on the tests that need a backend.
 *
 * Set it to `1` to let `ui/globalSetup.ts` start a throwaway backend (its own
 * library, random port, INDI simulator) and run the marked test files against it.
 * Without it those files are skipped and every request to the backend's port is
 * refused. It must never be used to point tests at a backend that is connected
 * to real hardware or the real library: the tests only ever use the backend the
 * run started itself.
 */
export const LIVE_BACKEND_TESTS_VARIABLE = 'ASTROMETRICS_LIVE_BACKEND_TESTS';
