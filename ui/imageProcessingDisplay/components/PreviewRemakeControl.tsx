/**
 * @file PreviewRemakeControl.tsx
 * @description Lets a person make a stack's preview picture again without stacking. It sits
 * behind a "Make the picture again" disclosure in the Stack Summary block. The person can
 * choose noise removal, its strength and star toning for one run; nothing is saved to the
 * settings. When the picture is ready, the block says what made it, using the small record
 * the backend saved beside the stack, and the image viewer shows it.
 */
import React, { useEffect, useRef, useState } from 'react';
import { UpdatingBar } from '../../common/components/Skeleton';
import { NumberInput } from '../../common/components/NumberInput';
import { PopoverSelect } from '../../common/components/PopoverSelect';
import { emit as emitEvent } from '../../common/utils/eventBus';
import {
    previewRemakeResultOf,
    STACK_PICTURE_REMADE_EVENT,
    startPreviewRemake,
    type PreviewRemakeOptions,
    type StackKind,
} from '../../common/services/imaging/stackReviewService';
import type { PreviewRemakeResult, PreviewSettingsRecord } from '../../common/types/backendTypes';
import { useReviewJob } from '../hooks/useReviewJob';
import { Button } from '../../common/components/Button';

import { Skeleton } from '../../common/components/Skeleton';
/** A choice for one setting: use the app setting, or force it on or off for this run. */
export type RunChoice = 'app' | 'on' | 'off';

interface PreviewRemakeControlProps {
    /** The target whose stack to make a picture of. */
    targetId: string;
    /** The imaging stack or the spectral stack. */
    kind: StackKind;
    /** Called with the stack's path when the new picture is on display, so the viewer can open it. */
    onShowPicture?: (stackPath: string) => void;
}

/** The three choices the pickers offer for each setting. */
const RUN_CHOICE_OPTIONS = [
    { id: 'app', label: 'Use the app setting' },
    { id: 'on', label: 'On' },
    { id: 'off', label: 'Off' },
];

/**
 * Turns a run choice into the value the backend takes.
 * @param choice The choice.
 * @return `true` or `false`, or `undefined` to use the app setting.
 */
export const choiceToFlag = (choice: RunChoice): boolean | undefined =>
    choice === 'app' ? undefined : choice === 'on';

/**
 * Checks the strength a person typed.
 * @param text The text in the strength box. Empty means the app setting.
 * @return The strength, `undefined` for empty text, or `null` if the text is not a number from 0 to 1.
 */
export const parseStrength = (text: string): number | undefined | null => {
    if (text.trim() === '') return undefined;
    const value = Number(text);
    return Number.isFinite(value) && value >= 0 && value <= 1 ? value : null;
};

/**
 * Says in plain words what made a preview picture.
 * @param record The record saved beside the stack, or `null` when there is none.
 * @return One sentence for each fact. A single "unknown" sentence when there is no record.
 */
export const describePreviewSettings = (record: PreviewSettingsRecord | null | undefined): string[] => {
    if (!record) return ['What made this picture is unknown. No record was saved with it.'];
    const used = record.settings_used;
    const lines: string[] = [];
    if (used.bright_object) {
        lines.push('This is a bright object, so it used its own stretch, with no noise removal or star toning.');
    } else {
        lines.push(
            used.denoise
                ? `Noise removal (Cosmic Clarity) was on${
                      used.denoise_strength == null ? '' : `, at strength ${used.denoise_strength}`
                  }.`
                : 'Noise removal was off.'
        );
        lines.push(used.star_toning ? 'Star toning was on.' : 'Star toning was off.');
        if (used.starnet) lines.push('Stars were stretched on their own (StarNet).');
    }
    lines.push(record.custom ? 'These choices were for that run only.' : 'These were the app settings.');
    return lines;
};

/**
 * The "Make the picture again" disclosure.
 */
export const PreviewRemakeControl: React.FC<PreviewRemakeControlProps> = ({ targetId, kind, onShowPicture }) => {
    const [denoise, setDenoise] = useState<RunChoice>('app');
    const [strengthText, setStrengthText] = useState('');
    const [starToning, setStarToning] = useState<RunChoice>('app');
    const { state, run } = useReviewJob<PreviewRemakeResult>({
        targetId,
        jobType: 'preview',
        readResult: previewRemakeResultOf,
    });

    const strength = parseStrength(strengthText);
    const strengthInvalid = strength === null;
    const busy = state.phase === 'starting' || state.phase === 'running';

    // When a picture is ready and on display, tell the viewer to read it again.
    const announcedRef = useRef<PreviewRemakeResult | null>(null);
    useEffect(() => {
        const result = state.result;
        if (!result || announcedRef.current === result) return;
        announcedRef.current = result;
        if (result.shown_in_viewer) {
            emitEvent('targetsUpdated', { id: targetId });
            emitEvent(STACK_PICTURE_REMADE_EVENT, { targetId, stackPath: result.stack_path });
            onShowPicture?.(result.stack_path);
        }
    }, [state.result, targetId, onShowPicture]);

    const start = () => {
        if (strengthInvalid) return;
        const options: PreviewRemakeOptions = {
            denoise: choiceToFlag(denoise),
            denoiseStrength: denoise === 'off' ? undefined : strength,
            starToning: choiceToFlag(starToning),
        };
        void run(() => startPreviewRemake(targetId, kind, options));
    };

    const result = state.phase === 'done' ? state.result : null;
    const idPrefix = `preview-remake-${kind}`;

    return (
        <details className="stack-summary__details preview-remake">
            <summary>Make the picture again</summary>
            <p className="stack-summary--muted">
                Makes the preview picture again from the stack that is there. The stack is not changed.
                Your choices apply to this one run only and are not saved.
            </p>
            <div className="preview-remake__fields">
                <span>Noise removal</span>
                <PopoverSelect
                    label="Noise removal"
                    options={RUN_CHOICE_OPTIONS}
                    selectedId={denoise}
                    onSelect={(id) => setDenoise((id ?? 'app') as RunChoice)}
                    disabled={busy}
                />

                <label htmlFor={`${idPrefix}-strength`}>Noise removal strength</label>
                <NumberInput
                    id={`${idPrefix}-strength`}
                    min={0}
                    max={1}
                    step={0.05}
                    placeholder="App setting"
                    value={strengthText}
                    onChange={(event) => setStrengthText(event.target.value)}
                    disabled={busy || denoise === 'off'}
                    aria-invalid={strengthInvalid}
                    aria-describedby={`${idPrefix}-strength-hint`}
                />
                <span id={`${idPrefix}-strength-hint`} className="stack-summary--muted preview-remake__hint">
                    {strengthInvalid ? (
                        <span className="stack-summary--error">Enter a number from 0 to 1.</span>
                    ) : (
                        'From 0 (none) to 1 (most). Leave empty for the app setting.'
                    )}
                </span>

                <span>Star toning</span>
                <PopoverSelect
                    label="Star toning"
                    options={RUN_CHOICE_OPTIONS}
                    selectedId={starToning}
                    onSelect={(id) => setStarToning((id ?? 'app') as RunChoice)}
                    disabled={busy}
                />
            </div>

            <Button
                variant="secondary"
                size="small"
                onClick={start}
                disabled={busy || strengthInvalid}
            >
                Make picture again
            </Button>

            <div className="preview-remake__status" role="status" aria-live="polite">
                {busy && (
                    <span>
                        Making the picture. This can take a minute.
                        <UpdatingBar label="Making the picture" inline />
                    </span>
                )}
                {result && (
                    <div className="preview-remake__result">
                        <div>
                            New picture made{' '}
                            {result.preview_settings
                                ? new Date(result.preview_settings.made_at).toLocaleString()
                                : 'just now'}
                            .
                        </div>
                        {!result.shown_in_viewer && (
                            <div className="stack-summary__flag">
                                The picture was made, but a picture you attached is on display, so the
                                viewer still shows that one.
                            </div>
                        )}
                        <ul>
                            {describePreviewSettings(result.preview_settings).map((line) => (
                                <li key={line}>{line}</li>
                            ))}
                        </ul>
                        {(result.preview_settings?.steps_run ?? []).length > 0 && (
                            <details className="stack-summary__details">
                                <summary>Steps that ran</summary>
                                <ul>
                                    {(result.preview_settings?.steps_run ?? []).map((step) => (
                                        <li key={step}>{step}</li>
                                    ))}
                                </ul>
                            </details>
                        )}
                    </div>
                )}
            </div>
            {state.phase === 'failed' && (
                <div className="stack-summary--error" role="alert">
                    Could not make the picture: {state.message}
                </div>
            )}
        </details>
    );
};
