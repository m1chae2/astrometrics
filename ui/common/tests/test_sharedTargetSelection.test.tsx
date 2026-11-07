/**
 * @file test_sharedTargetSelection.test.tsx
 * @description Tests that every target-based display shares one selected target: a pick in one
 * view shows in another, a reload keeps it, a fresh start picks the most recently imaged target
 * that has an image, and a kept choice that no longer exists falls back to that default.
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import { TargetProvider, useTargetContext } from '../context/TargetContext';
import { resetStartupSelectionCheck, useTargetListLogic } from '../hooks/useTargetListLogic';

const queryState = vi.hoisted(() => ({
    targets: [] as unknown[],
    cameraIndex: { cameras: [], targets: {} } as unknown,
}));

vi.mock('../queries/useTargetListQuery', () => ({
    useTargetListQuery: () => ({ data: queryState.targets, isLoading: false, isError: false, refetch: vi.fn(), error: null }),
}));
vi.mock('../queries/useAstronomyListQuery', () => ({
    useAstronomyListQuery: () => ({ data: [], isLoading: false, refetch: vi.fn(), error: null }),
}));
vi.mock('../queries/useTargetCameraIndexQuery', () => ({
    useTargetCameraIndexQuery: () => ({ data: queryState.cameraIndex, isLoading: false, refetch: vi.fn() }),
}));
vi.mock('../utils/socketClient', () => ({ socketClient: { on: vi.fn(), off: vi.fn() } }));

/** Builds a library target; `image` false leaves it without a stacked image. */
const makeTarget = (id: string, image: boolean) => ({
    id, name: id, objectType: 'Messier', stacking: image ? { stackedImage: `/stacks/${id}.fits` } : {},
});

/** One display: runs the shared list logic like a view does and shows its selection. */
const FakeDisplay: React.FC<{ label: string }> = ({ label }) => {
    const { selectedTarget, setSelectedTarget, pendingTarget, setPendingTarget, reloadKey } = useTargetContext();
    useTargetListLogic(reloadKey, pendingTarget, selectedTarget, setPendingTarget, setSelectedTarget);
    return (
        <div>
            <span data-testid={`${label}-selected`}>{selectedTarget}</span>
            <button onClick={() => setSelectedTarget('M 31')}>{`${label} pick`}</button>
        </div>
    );
};

/** Renders two displays under one provider. */
const renderTwoDisplays = () => render(
    <TargetProvider><FakeDisplay label="viewer" /><FakeDisplay label="processing" /></TargetProvider>
);

describe('shared target selection', () => {
    beforeEach(() => {
        localStorage.clear();
        resetStartupSelectionCheck();
        queryState.targets = [
            makeTarget('Albireo', false),
            makeTarget('M 13', true),
            makeTarget('M 31', true),
            makeTarget('M 57', true),
        ];
        queryState.cameraIndex = {
            cameras: [],
            targets: {
                'M 13': { lastFrameTime: 100, cameras: {} },
                'M 31': { lastFrameTime: 300, cameras: {} },
                'M 57': { lastFrameTime: 200, cameras: {} },
            },
        };
    });

    it('starts on the most recently imaged target that has an image, in every display', () => {
        renderTwoDisplays();
        expect(screen.getByTestId('viewer-selected')).toHaveTextContent('M 31');
        expect(screen.getByTestId('processing-selected')).toHaveTextContent('M 31');
    });

    it('never defaults to a target without an image when one with an image exists', () => {
        queryState.cameraIndex = { cameras: [], targets: { Albireo: { lastFrameTime: 999, cameras: {} }, 'M 13': { lastFrameTime: 1, cameras: {} } } };
        renderTwoDisplays();
        expect(screen.getByTestId('viewer-selected')).toHaveTextContent('M 13');
    });

    it('carries a pick in one display to the other and keeps it after a reload', () => {
        queryState.cameraIndex = { cameras: [], targets: { 'M 13': { lastFrameTime: 500, cameras: {} } } };
        const first = renderTwoDisplays();
        expect(screen.getByTestId('viewer-selected')).toHaveTextContent('M 13');
        act(() => { screen.getByText('viewer pick').click(); });
        expect(screen.getByTestId('processing-selected')).toHaveTextContent('M 31');
        first.unmount();
        // A reload: a new page load starts with a fresh startup check.
        resetStartupSelectionCheck();
        renderTwoDisplays();
        expect(screen.getByTestId('processing-selected')).toHaveTextContent('M 31');
    });

    it('falls back to the default when the kept choice no longer exists', () => {
        localStorage.setItem('selectedTarget', 'Gone Target');
        renderTwoDisplays();
        expect(screen.getByTestId('viewer-selected')).toHaveTextContent('M 31');
    });
});
