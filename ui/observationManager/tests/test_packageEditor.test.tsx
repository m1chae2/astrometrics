/**
 * @file test_packageEditor.test.tsx
 * @description Tests of the package editor: the empty state, the exposure rows of a passed
 * package, adding and removing rows, and Add to Sequence.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PackageEditor, PackageEditorProps } from '../PackageEditor';
import { FilterType, FrameType, ObservationPackage } from '../../common/types/backendTypes';

vi.mock('../LastImageViewer', () => ({ LastImageViewer: () => <div>Last Image stub</div> }));
vi.mock('../../common/services/imaging/captureService', () => ({ captureTestFrame: vi.fn() }));

const samplePackage: ObservationPackage = {
    id: 'p1', name: 'M 81', target_id: 'M 81',
    exposure_requests: [{ frameType: FrameType.LIGHT, filter: FilterType.Ha, count: 10, exposureSec: 300 }],
    dither_config: { enabled: true, everyNFrames: 3, pixels: 4 },
};

/** Renders the editor with mock callbacks. */
function renderEditor(editedPackage: ObservationPackage | null, overrides: Partial<PackageEditorProps> = {}) {
    const props: PackageEditorProps = {
        editedPackage,
        onAddExposure: vi.fn(), onUpdateExposure: vi.fn(), onRemoveExposure: vi.fn(),
        onChangeDither: vi.fn(), onAddToSequence: vi.fn(),
        ...overrides,
    };
    render(<PackageEditor {...props} />);
    return props;
}

describe('PackageEditor', () => {
    it('shows the empty state in all three panels when no package is chosen', () => {
        renderEditor(null);
        expect(screen.getAllByText('Choose a package or New package')).toHaveLength(3);
        expect(screen.queryByRole('button', { name: 'Add to Sequence' })).not.toBeInTheDocument();
    });

    it('shows the exposure rows of the passed package', () => {
        renderEditor(samplePackage);
        const table = screen.getByRole('table');
        expect(within(table).getByText('300s')).toBeInTheDocument();
        expect(within(table).getByText('Ha')).toBeInTheDocument();
    });

    it('adds a row through the Image Planner', async () => {
        const props = renderEditor(samplePackage);
        await userEvent.click(screen.getByRole('button', { name: 'Add to List' }));
        expect(props.onAddExposure).toHaveBeenCalledWith(expect.objectContaining({ frameType: FrameType.LIGHT, count: 1, exposureSec: 60 }));
    });

    it('removes a row', async () => {
        const props = renderEditor(samplePackage);
        await userEvent.click(screen.getByTitle('Remove Item'));
        expect(props.onRemoveExposure).toHaveBeenCalledWith(0);
    });

    it('edits the dither of the package', async () => {
        const props = renderEditor(samplePackage);
        await userEvent.click(screen.getByRole('checkbox'));
        expect(props.onChangeDither).toHaveBeenCalledWith({ enabled: false, everyNFrames: 3, pixels: 4 });
    });

    it('adds the package to the sequence', async () => {
        const props = renderEditor(samplePackage);
        await userEvent.click(screen.getByRole('button', { name: 'Add to Sequence' }));
        expect(props.onAddToSequence).toHaveBeenCalledWith(samplePackage);
    });

    it('shows a refused save as an alert', () => {
        renderEditor(samplePackage, { errorMessage: 'Could not save the package: revision conflict' });
        expect(screen.getByRole('alert')).toHaveTextContent('revision conflict');
    });
});
