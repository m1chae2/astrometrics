/**
 * @fileoverview The tonight candidates, drawn the same way in every display:
 * for each use (imaging, photometry, spectroscopy) the list the library
 * returned with its gain description, the top three candidates and
 * "Show all N".
 *
 * Each candidate shows its status (an icon AND a word), the reason, the
 * thin-dataset note and the Tentative chip. The display decides which
 * actions each candidate offers ("Switch to", "Open in Planner",
 * "Show on sky") by passing the matching callbacks. Nothing here ranks or
 * sorts: the lists and the candidates are drawn in the order the library
 * returned them. The ranking's own `at` and `generated_at` times are shown.
 */

import React, { useRef, useState } from 'react';
import type { UseQueryResult } from '@tanstack/react-query';
import { CandidateStatus, Confidence, RankedUse } from '../../types/backendTypes';
import type { RankedCandidate, RankedList, TonightRanking } from '../../types/backendTypes';
import { formatPlanningTime } from '../PlanningTimeIndicator';
import { ConfidenceChip } from './ConfidenceChip';
import { QualityPanelState } from './QualityPanelState';
import { formatValue } from './statementLabels';
import './quality.css';

/** How many candidates each list shows before "Show all N". */
const TOP_CANDIDATE_COUNT = 3;

/** The actions a display offers on each candidate. A button shows only when its callback is given. */
export interface CandidateActions {
    /** "Switch to": makes the candidate the current target. */
    onSwitch?: (targetId: string) => void;
    /** "Open in Planner": opens the candidate in the Observation Planner. */
    onOpenInPlanner?: (targetId: string) => void;
    /** "Show on sky": centers the Planetarium on the candidate. */
    onShowOnSky?: (targetId: string) => void;
}

/**
 * Gives the word for a use.
 * @param use The use.
 * @return The word.
 */
function useWord(use: RankedUse): string {
    switch (use) {
        case RankedUse.IMAGING:
            return 'Imaging';
        case RankedUse.PHOTOMETRY:
            return 'Photometry';
        case RankedUse.SPECTROSCOPY:
            return 'Spectroscopy';
        default:
            return String(use);
    }
}

/**
 * Gives the word for a candidate status.
 * @param status The status.
 * @return The word.
 */
function statusWord(status: CandidateStatus): string {
    switch (status) {
        case CandidateStatus.RANKED:
            return 'Ranked';
        case CandidateStatus.STOPPED:
            return 'Stopped';
        case CandidateStatus.NEW:
            return 'New';
        case CandidateStatus.NO_FORECAST:
            return 'No forecast';
        default:
            return String(status);
    }
}

/**
 * Draws the icon of a candidate status. The status word is shown beside it.
 * @param props The status.
 * @return The icon.
 */
const StatusIcon: React.FC<{ status: CandidateStatus }> = ({ status }) => {
    let shape: React.ReactNode;
    switch (status) {
        case CandidateStatus.RANKED:
            shape = <path d="M4 20 V12 M12 20 V6 M20 20 V10" />;
            break;
        case CandidateStatus.STOPPED:
            shape = <rect x="6" y="6" width="12" height="12" />;
            break;
        case CandidateStatus.NEW:
            shape = <path d="M12 3 V9 M12 15 V21 M3 12 H9 M15 12 H21" />;
            break;
        default:
            shape = <path d="M9 9 C9 5 15 5 15 9 C15 12 12 12 12 15 M12 18.5 V19" />;
    }
    return (
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" aria-hidden="true" focusable="false">
            {shape}
        </svg>
    );
};

/**
 * Writes the gain of a candidate for display.
 * @param candidate The candidate.
 * @return Text such as `18% noise cut per hour`, or an empty string when there is no gain.
 */
function gainText(candidate: RankedCandidate): string {
    if (candidate.gainPerHourFraction === null || candidate.gainPerHourFraction === undefined) return '';
    return `${formatValue(candidate.gainPerHourFraction * 100)}% noise cut per hour`;
}

/**
 * One candidate.
 * @param props The candidate and the actions to offer.
 * @return The list item.
 */
const CandidateRow: React.FC<{ candidate: RankedCandidate } & CandidateActions> = ({
    candidate,
    onSwitch,
    onOpenInPlanner,
    onShowOnSky,
}) => {
    const word = statusWord(candidate.status);
    const gain = gainText(candidate);
    return (
        <li className="candidate" data-status={candidate.status}>
            <div className="candidate__head">
                {candidate.rank ? <span className="candidate__rank">{candidate.rank}.</span> : null}
                <strong>{candidate.targetId}</strong>
                <span className="candidate__status">
                    <StatusIcon status={candidate.status} /> {word}
                </span>
                <ConfidenceChip confidence={candidate.confidence ?? Confidence.DESIGN_ESTIMATE} />
                {gain && <span>{gain}</span>}
            </div>
            {candidate.reason && <p className="candidate__reason">{candidate.reason}</p>}
            {candidate.thinDataset && (
                <p className="candidate__reason">Thin dataset: part of this gain is just a short history.</p>
            )}
            <div className="candidate__buttons">
                {onShowOnSky && (
                    <button
                        type="button"
                        className="btn btn--tiny"
                        aria-label={`Show on sky: ${candidate.targetId}`}
                        onClick={() => onShowOnSky(candidate.targetId)}
                    >
                        Show on sky
                    </button>
                )}
                {onSwitch && (
                    <button
                        type="button"
                        className="btn btn--tiny"
                        aria-label={`Switch to ${candidate.targetId}`}
                        onClick={() => onSwitch(candidate.targetId)}
                    >
                        Switch to
                    </button>
                )}
                {onOpenInPlanner && (
                    <button
                        type="button"
                        className="btn btn--tiny"
                        aria-label={`Open in Planner: ${candidate.targetId}`}
                        onClick={() => onOpenInPlanner(candidate.targetId)}
                    >
                        Open in Planner
                    </button>
                )}
            </div>
        </li>
    );
};

/**
 * One use's list with its gain description and "Show all N".
 * @param props The list and the actions to offer.
 * @return The list section.
 */
const CandidateList: React.FC<{ list: RankedList } & CandidateActions> = ({ list, ...actions }) => {
    const [showAll, setShowAll] = useState(false);
    const toggleReference = useRef<HTMLButtonElement>(null);
    const candidates = list.candidates ?? [];
    const shown = showAll ? candidates : candidates.slice(0, TOP_CANDIDATE_COUNT);
    const word = useWord(list.use);
    return (
        <section className="candidate-list" aria-label={`${word} candidates`}>
            <h4>{word}</h4>
            <p className="candidate-list__gain">{list.gainDescription}</p>
            {candidates.length === 0 ? (
                <p className="quality-state">No candidates for this use.</p>
            ) : (
                <ul className="candidate-list__items">
                    {shown.map((candidate) => (
                        <CandidateRow key={`${candidate.use}-${candidate.targetId}`} candidate={candidate} {...actions} />
                    ))}
                </ul>
            )}
            {candidates.length > TOP_CANDIDATE_COUNT && (
                <button
                    ref={toggleReference}
                    type="button"
                    className="btn btn--tiny"
                    aria-expanded={showAll}
                    onClick={() => {
                        setShowAll((value) => !value);
                        toggleReference.current?.focus();
                    }}
                >
                    {showAll ? 'Show fewer' : `Show all ${candidates.length}`}
                </button>
            )}
        </section>
    );
};

/**
 * Draws the candidates of one ranking: its times, each use's list and its notes.
 * @param props The ranking and the actions to offer.
 * @return The lists, or the empty state when the ranking has no list.
 */
export const RankedCandidates: React.FC<{ ranking: TonightRanking } & CandidateActions> = ({ ranking, ...actions }) => {
    const lists = ranking.lists ?? [];
    const notes = ranking.notes ?? [];
    if (lists.length === 0) {
        return <QualityPanelState state="empty" emptyMessage="No candidates yet. Sync to fetch new frames." />;
    }
    return (
        <>
            <p className="candidates__times">
                Ranked for {formatPlanningTime(ranking.at)}
                {ranking.generatedAt ? `, computed ${formatPlanningTime(ranking.generatedAt)}` : ''}
            </p>
            {lists.map((list) => (
                <CandidateList key={list.use} list={list} {...actions} />
            ))}
            {notes.length > 0 && (
                <ul className="candidates__notes" aria-label="Ranking notes">
                    {notes.map((note) => (
                        <li key={note}>{note}</li>
                    ))}
                </ul>
            )}
        </>
    );
};

/**
 * Draws the candidates of a tonight-ranking query in all its states: loading,
 * error with "Try again", empty, or the lists.
 * @param props The query result and the actions to offer.
 * @return The body of a candidates panel.
 */
export const TonightRankingBody: React.FC<{ query: UseQueryResult<TonightRanking> } & CandidateActions> = ({
    query,
    ...actions
}) => {
    if (query.isLoading) return <QualityPanelState state="loading" />;
    if (query.isError) {
        return <QualityPanelState state="error" subject="candidate ranking" onRetry={() => void query.refetch()} />;
    }
    if (!query.data) return <QualityPanelState state="empty" emptyMessage="No candidates yet. Sync to fetch new frames." />;
    return <RankedCandidates ranking={query.data} {...actions} />;
};
