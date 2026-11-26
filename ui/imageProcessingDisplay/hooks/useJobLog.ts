/**
 * @file useJobLog.ts
 * @description The one way the job log panel follows a job's log. It works from a job id and
 * whether that job is running, never from the kind of job. A running job's log is streamed live;
 * a job that is not running (finished, failed or cancelled) shows its whole stored log, read once.
 * A job kind added later is followed the same way with no change here. A new stream of the same
 * job replays the log from its start, so the hook counts the streamed lines already shown and
 * skips that many: a line that really repeats (the same message twice) is still shown.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchJobLogTail, streamJobLog } from '../../common/services/imaging/processingService';

/** How many stored lines a finished job's log shows. */
export const FINISHED_LOG_LINES = 5000;

/** What the hook gives the panel. */
export interface JobLog {
    /** The log lines to show, oldest first. */
    lines: string[];
    /** Empties the panel. */
    clear: () => void;
    /** Adds a line the app wrote itself (for example a start or error note), unless it is already the last line. */
    appendLocal: (line: string) => void;
}

/**
 * Follows the log of one job by its id.
 * @param jobId The job whose log is shown, or `null` for none.
 * @param isRunning True while the job runs: its log is streamed. Otherwise the stored log is read once.
 * @return The lines and two functions to change them by hand.
 */
export function useJobLog(jobId: string | null, isRunning: boolean): JobLog {
    const [lines, setLines] = useState<string[]>([]);
    /** The job and mode that were on screen last, to read the full log once when a job ends. */
    const lastRef = useRef<{ jobId: string | null; running: boolean }>({ jobId: null, running: false });
    /** How many lines of the current job's log are on screen, so a replayed stream skips them. */
    const shownLogLinesRef = useRef(0);

    useEffect(() => {
        const previous = lastRef.current;
        lastRef.current = { jobId, running: isRunning };
        if (!jobId) return undefined;
        let cancelled = false;
        let stopStream: (() => void) | undefined;
        if (isRunning) {
            // The stream replays the job's whole log, so start from an empty panel when
            // the job changes. The same job keeps its lines.
            if (previous.jobId !== jobId) {
                setLines([]);
                shownLogLinesRef.current = 0;
            }
            // The position of each line in this stream, counted from the start of the log.
            let position = 0;
            streamJobLog(jobId, (line) => {
                if (cancelled) return;
                position += 1;
                if (position <= shownLogLinesRef.current) return;
                shownLogLinesRef.current = position;
                setLines((current) => [...current, line]);
            }).then((stop) => {
                if (cancelled) stop();
                else stopStream = stop;
            });
        } else {
            // Not running: the whole stored log, including lines the stream had not delivered yet.
            if (previous.jobId !== jobId) {
                setLines([]);
                shownLogLinesRef.current = 0;
            }
            fetchJobLogTail(jobId, FINISHED_LOG_LINES).then((stored) => {
                if (cancelled || stored.length === 0) return;
                shownLogLinesRef.current = stored.length;
                setLines(stored);
            });
        }
        return () => {
            cancelled = true;
            if (stopStream) stopStream();
        };
    }, [jobId, isRunning]);

    const clear = useCallback(() => setLines([]), []);
    // Two parts of the screen can note the same event (a job's end) at the same moment, so a
    // local note equal to the last line is not added twice.
    const appendLocal = useCallback(
        (line: string) => setLines((current) => (current[current.length - 1] === line ? current : [...current, line])),
        [],
    );
    return { lines, clear, appendLocal };
}
