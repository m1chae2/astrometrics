/**
 * @file JobHistoryList.tsx
 * @description Component for displaying a list of historical processing jobs for a target.
 * REQ: IMG-5: Automated Stacking Pipeline
 */
import React from 'react';
import { ProcessingJob } from '../../common/types/backendTypes';
import { LoadingSpinner } from '../../common/components/LoadingSpinner';

interface JobHistoryListProps {
    jobs: ProcessingJob[];
    loading?: boolean;
    onSelectJob?: (job: ProcessingJob) => void;
    onDismissJob?: (jobId: string) => Promise<void>;
    activeJobId?: string | null;
}

export const JobHistoryList: React.FC<JobHistoryListProps> = ({
    jobs,
    loading,
    onSelectJob,
    onDismissJob,
    activeJobId
}) => {
    if (loading && jobs.length === 0) {
        return <div className="job-history-list__loading"><LoadingSpinner /> Loading job history...</div>;
    }

    if (jobs.length === 0) {
        return <div className="job-history-list__empty">No historical jobs found for this target.</div>;
    }

    return (
        <div className="job-history-list">
            <h4 className="job-history-list__title">Recent Jobs</h4>
            <div className="job-history-list__items">
                {jobs.map(job => (
                    <div
                        key={job.id}
                        className={`job-history-item job-history-item--${job.status} ${activeJobId === job.id ? 'job-history-item--active' : ''}`}
                        onClick={() => onSelectJob?.(job)}
                    >
                        <div className="job-history-item__header">
                            <span className="job-history-item__type">{job.jobType}</span>
                            <div className="job-history-item__actions">
                                <span className={`job-history-item__status-pill status-pill--${job.status}`}>
                                    {job.status}
                                </span>
                                <button
                                    className="job-history-item__dismiss"
                                    onClick={(e) => {
                                        e.stopPropagation();
                                        onDismissJob?.(job.id);
                                    }}
                                    title="Dismiss Job"
                                >
                                    &times;
                                </button>
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
                ))}
            </div>
        </div>
    );
};
