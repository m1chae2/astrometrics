/**
 * @file JobHistoryList.tsx
 * @description Lists the processing jobs for a target. It shows the current observing night
 * (local noon to local noon) with any running job pinned on top. Older nights load only when
 * the user asks, a page at a time, each under its own night heading.
 * REQ: IMG-5: Automated Stacking Pipeline
 */
import React, { useEffect, useMemo, useState } from 'react';
import { ProcessingJob } from '../../common/types/backendTypes';
import { Button } from '../../common/components/Button';
import { LoadingSpinner } from '../../common/components/LoadingSpinner';
import { fetchJobs } from '../../common/services/imaging/processingService';
import {
    getNightStart, groupJobsByNight, isFromCurrentNight, toLocalIsoTime,
} from '../utils/jobNights';

/** How many earlier jobs one click of "Earlier nights" or "Show more" loads. */
export const EARLIER_JOBS_PAGE_SIZE = 30;

interface JobHistoryListProps {
    /** The target whose earlier nights can be loaded. */
    targetId?: string;
    /** The newest jobs for the target. Only those from the current night are shown by default. */
    jobs: ProcessingJob[];
    /** Jobs running right now for the target. They always show at the top, whatever their age. */
    pinnedJobs?: ProcessingJob[];
    loading?: boolean;
    onSelectJob?: (job: ProcessingJob) => void;
    activeJobId?: string | null;
    /** The current time. Only tests set this. */
    now?: Date;
}

/**
 * Draws one job row: type, status chip, Console button, date and message.
 *
 * @param {object} props - Component props.
 * @param {ProcessingJob} props.job - The job to draw.
 * @param {boolean} props.isSelected - Whether this job's log is the one on screen.
 * @param {(job: ProcessingJob) => void} [props.onSelectJob] - Called when the row is chosen.
 * @returns {React.ReactElement} The row.
 */
const JobRow: React.FC<{
    job: ProcessingJob;
    isSelected: boolean;
    onSelectJob?: (job: ProcessingJob) => void;
}> = ({ job, isSelected, onSelectJob }) => (
    <div
        role="listitem"
        tabIndex={0}
        className={`job-history-item job-history-item--${job.status} ${isSelected ? 'job-history-item--active' : ''}`}
        onClick={() => onSelectJob?.(job)}
        onKeyDown={(event) => {
            if (event.target !== event.currentTarget) return;
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                onSelectJob?.(job);
            }
        }}
    >
        <div className="job-history-item__header">
            <span className="job-history-item__type">{job.jobType}</span>
            <div className="job-history-item__actions">
                <span className={`job-history-item__status-pill status-pill--${job.status}`}>
                    {job.status}
                </span>
                <Button
                    variant="secondary"
                    size="small"
                    className="job-history-item__inspect"
                    onClick={(e) => {
                        e.stopPropagation();
                        window.dispatchEvent(
                            new CustomEvent('astrometrics:modeChange', {
                                detail: 'Command Console'
                            })
                        );
                    }}
                    title="Inspect in Command Console"
                >
                    Console
                </Button>
            </div>
        </div>
        <div className="job-history-item__meta">
            <span className="job-history-item__date">
                {job.createdAt ? new Date(job.createdAt).toLocaleString() : 'Unknown date'}
            </span>
        </div>
        {job.message && (
            <div className="job-history-item__message">{job.message}</div>
        )}
    </div>
);

/**
 * Shows the current night's jobs, with running jobs pinned, and loads earlier nights on demand.
 *
 * @param {JobHistoryListProps} props - Component props.
 * @returns {React.ReactElement} The list.
 */
export const JobHistoryList: React.FC<JobHistoryListProps> = ({
    targetId,
    jobs,
    pinnedJobs = [],
    loading,
    onSelectJob,
    activeJobId,
    now,
}) => {
    const [earlierJobs, setEarlierJobs] = useState<ProcessingJob[]>([]);
    const [earlierOpen, setEarlierOpen] = useState<boolean>(false);
    const [loadingEarlier, setLoadingEarlier] = useState<boolean>(false);
    const [hasMoreEarlier, setHasMoreEarlier] = useState<boolean>(true);

    // Forget earlier nights when the user switches target.
    useEffect(() => {
        setEarlierJobs([]);
        setEarlierOpen(false);
        setHasMoreEarlier(true);
    }, [targetId]);

    const currentTime = now ?? new Date();
    const tonightRows = useMemo(() => {
        const pinnedIds = new Set(pinnedJobs.map((job) => job.id));
        const tonightJobs = jobs.filter((job) => !pinnedIds.has(job.id) && isFromCurrentNight(job, currentTime));
        return [...pinnedJobs, ...tonightJobs];
        // currentTime changes every render when `now` is not given; the inputs that matter are listed.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [jobs, pinnedJobs, now]);

    const earlierNights = useMemo(() => {
        const shownIds = new Set(tonightRows.map((job) => job.id));
        return groupJobsByNight(earlierJobs.filter((job) => !shownIds.has(job.id)));
    }, [earlierJobs, tonightRows]);

    /**
     * Loads the next page of earlier jobs: those created before the oldest job
     * already loaded, or before tonight's start for the first page.
     */
    const loadEarlier = async (): Promise<void> => {
        if (!targetId || loadingEarlier) return;
        setLoadingEarlier(true);
        setEarlierOpen(true);
        const oldestLoaded = earlierJobs
            .map((job) => job.createdAt)
            .filter((createdAt): createdAt is string => !!createdAt)
            .sort()[0];
        const before = oldestLoaded ?? toLocalIsoTime(getNightStart(currentTime));
        const page = await fetchJobs(targetId, undefined, { before, limit: EARLIER_JOBS_PAGE_SIZE });
        setEarlierJobs((previous) => [...previous, ...page]);
        setHasMoreEarlier(page.length >= EARLIER_JOBS_PAGE_SIZE);
        setLoadingEarlier(false);
    };

    /**
     * Folds the list back to tonight's jobs. The loaded pages are dropped, so
     * opening the earlier nights again fetches them fresh.
     */
    const hideEarlier = (): void => {
        setEarlierOpen(false);
        setEarlierJobs([]);
        setHasMoreEarlier(true);
    };

    if (loading && jobs.length === 0 && pinnedJobs.length === 0) {
        return <div className="job-history-list__loading"><LoadingSpinner /> Loading job history...</div>;
    }

    const renderRows = (rows: ProcessingJob[]): React.ReactNode =>
        rows.map((job) => (
            <JobRow key={job.id} job={job} isSelected={activeJobId === job.id} onSelectJob={onSelectJob} />
        ));

    return (
        <div className="job-history-list">
            <h4 className="job-history-list__title">Recent Jobs</h4>
            <div className="job-history-list__items" role="list" aria-label="Jobs from tonight">
                {tonightRows.length === 0 && (
                    <div className="job-history-list__empty">No jobs yet tonight.</div>
                )}
                {renderRows(tonightRows)}
                {earlierOpen && earlierNights.map((night) => (
                    <section key={night.nightKey} className="job-history-night">
                        <h5 className="job-history-night__heading">{night.label}</h5>
                        <div role="list" aria-label={`Jobs from ${night.label}`} className="job-history-night__items">
                            {renderRows(night.jobs)}
                        </div>
                    </section>
                ))}
                {earlierOpen && !loadingEarlier && earlierNights.length === 0 && (
                    <div className="job-history-list__empty">No earlier jobs for this target.</div>
                )}
            </div>
            {targetId && (
                <div className="job-history-list__controls">
                    {(!earlierOpen || (hasMoreEarlier && earlierJobs.length > 0)) && (
                        <Button
                            variant="ghost"
                            className="job-history-list__more"
                            disabled={loadingEarlier}
                            onClick={() => { void loadEarlier(); }}
                        >
                            {loadingEarlier
                                ? 'Loading...'
                                : earlierOpen ? 'Show more earlier nights' : 'Earlier nights ▸'}
                        </Button>
                    )}
                    {earlierOpen && !loadingEarlier && (
                        <Button variant="ghost" className="job-history-list__more" onClick={hideEarlier}>
                            Hide earlier nights ▾
                        </Button>
                    )}
                </div>
            )}
        </div>
    );
};
