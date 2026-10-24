/**
 * @fileoverview The re-ranked candidates at Now: for each use (imaging,
 * photometry, spectroscopy), the list the library returned with its gain
 * description, the top three candidates and "Show all N".
 *
 * Each candidate shows its status (an icon AND a word), the reason, the
 * thin-dataset note and the Tentative chip, with "Switch to" and "Open in
 * Planner". The panel does no ranking: it draws the lists in the order the
 * library returned them. The ranking's own `at` and `generated_at` times are
 * shown, because Execution always works at Now.
 */

import React, { useRef, useState } from 'react';
import { useTonightRanking } from '../common/queries/qualityAdvisoryQueries';
import { CandidateStatus, Confidence, RankedUse } from '../common/types/backendTypes';
import type { RankedCandidate, RankedList } from '../common/types/backendTypes';
import { useTargetContext } from '../common/context/TargetContext';
import { navigateToElement } from '../common/utils/displayCoordinator';
import { ConfidenceChip } from '../common/components/quality/ConfidenceChip';
import { QualityPanelState } from '../common/components/quality/QualityPanelState';
import { formatValue } from '../common/components/quality/statementLabels';
import { OBSERVATION_PLANNER_MODE } from '../common/components/quality/useStatementActions';
import { formatMoment } from './utils/formatDisplay';

/** How many candidates each list shows before "Show all N". */
const TOP_CANDIDATE_COUNT = 3;

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

interface CandidateRowProps {
    /** The candidate. */
    candidate: RankedCandidate;
    /** Called for "Switch to". */
    onSwitch: (targetId: string) => void;
    /** Called for "Open in Planner". */
    onOpenInPlanner: (targetId: string) => void;
}

/**
 * One candidate.
 * @param props The candidate and its two actions.
 * @return The list item.
 */
const CandidateRow: React.FC<CandidateRowProps> = ({ candidate, onSwitch, onOpenInPlanner }) => {
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
                <button
                    type="button"
                    className="btn btn--tiny"
                    aria-label={`Switch to ${candidate.targetId}`}
                    onClick={() => onSwitch(candidate.targetId)}
                >
                    Switch to
                </button>
                <button
                    type="button"
                    className="btn btn--tiny"
                    aria-label={`Open in Planner: ${candidate.targetId}`}
                    onClick={() => onOpenInPlanner(candidate.targetId)}
                >
                    Open in Planner
                </button>
            </div>
        </li>
    );
};

interface CandidateListProps extends Omit<CandidateRowProps, 'candidate'> {
    /** The ranked list of one use. */
    list: RankedList;
}

/**
 * One use's list with its gain description and "Show all N".
 * @param props The list and the two actions.
 * @return The list section.
 */
const CandidateList: React.FC<CandidateListProps> = ({ list, onSwitch, onOpenInPlanner }) => {
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
                        <CandidateRow
                            key={`${candidate.use}-${candidate.targetId}`}
                            candidate={candidate}
                            onSwitch={onSwitch}
                            onOpenInPlanner={onOpenInPlanner}
                        />
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
 * The tonight candidates panel.
 * @return The panel.
 */
export const TonightCandidatesPanel: React.FC = () => {
    const ranking = useTonightRanking(null);
    const { setSelectedTarget } = useTargetContext();

    const openInPlanner = (targetId: string) => {
        void navigateToElement({
            targetDisplay: OBSERVATION_PLANNER_MODE,
            targetElement: 'dataQuality',
            action: 'openInPlanner',
            payload: { targetId },
        });
    };

    let body: React.ReactNode;
    if (ranking.isLoading) body = <QualityPanelState state="loading" />;
    else if (ranking.isError) {
        body = <QualityPanelState state="error" subject="candidate ranking" onRetry={() => void ranking.refetch()} />;
    } else {
        const lists = ranking.data?.lists ?? [];
        const notes = ranking.data?.notes ?? [];
        body =
            lists.length === 0 ? (
                <QualityPanelState state="empty" emptyMessage="No candidates yet. Sync to fetch new frames." />
            ) : (
                <>
                    <p className="candidates__times">
                        Ranked for {formatMoment(ranking.data!.at)}
                        {ranking.data!.generatedAt ? `, computed ${formatMoment(ranking.data!.generatedAt)}` : ''}
                    </p>
                    {lists.map((list) => (
                        <CandidateList key={list.use} list={list} onSwitch={setSelectedTarget} onOpenInPlanner={openInPlanner} />
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
    }

    return (
        <section className="execution-section" aria-label="Candidates now">
            <h3 className="execution-section__title">
                Candidates now
                <button type="button" className="btn btn--tiny" onClick={() => void ranking.refetch()}>
                    Refresh
                </button>
            </h3>
            {body}
        </section>
    );
};
