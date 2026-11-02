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
import { captureTestFrame } from '../../common/services/imaging/captureService';
import { FilterType, FrameType, ObservationPackage } from '../../common/types/backendTypes';

vi.mock('../LastImageViewer', () => ({ LastImageViewer: () => <div>Last Image stub</div> }));
/** The configured filters `system:filters` returns, as the library names them. */
const configuredFilters = vi.hoisted(() => ({ list: ['Luminance', 'Star Analyzer 200'] as string[] }));
vi.mock('../hooks/useAvailableFilters', () => ({
    useAvailableFilters: () => ({ filters: configuredFilters.list, loading: false, error: null }),
}));
vi.mock('../../common/services/imaging/captureService', () => ({ captureTestFrame: vi.fn() }));

/** What the mocked policy answers; tests change it. */
const availability = vi.hoisted(() => ({ canCommand: true, reason: 'Checking whether Ekos is in control…' }));
vi.mock('../../common/hooks/useCommandAvailability', () => ({
    useCommandAvailability: () => ({ ...availability, delegations: [], isChecking: false }),
}));

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
    it('shows the empty state in both editor panels when no package is chosen', () => {
        renderEditor(null);
        expect(screen.getAllByText('Choose a package or New package')).toHaveLength(2);
        expect(screen.queryByRole('button', { name: 'Add to Sequence' })).not.toBeInTheDocument();
    });

    it('shows the exposure rows of the passed package', () => {
        renderEditor(samplePackage);
        const table = screen.getByRole('table');
        expect(within(table).getByText('300 s')).toBeInTheDocument();
        expect(within(table).getByText('Ha')).toBeInTheDocument();
    });

    it('adds a row through the Image Planner', async () => {
        const props = renderEditor(samplePackage);
        await userEvent.click(screen.getByRole('button', { name: 'Add to List' }));
        expect(props.onAddExposure).toHaveBeenCalledWith(expect.objectContaining({ frameType: FrameType.LIGHT, count: 1, exposureSec: 60 }));
    });

    it('removes a row', async () => {
        const props = renderEditor(samplePackage);
        await userEvent.click(screen.getByTitle('Remove row'));
        expect(props.onRemoveExposure).toHaveBeenCalledWith(0);
    });

    it('edits the dither of the package on the Dither tab', async () => {
        const props = renderEditor(samplePackage);
        await userEvent.click(screen.getByRole('tab', { name: 'Dither' }));
        await userEvent.click(screen.getByRole('checkbox', { name: 'Dither between frames' }));
        expect(props.onChangeDither).toHaveBeenCalledWith({ enabled: false, everyNFrames: 3, pixels: 4 });
    });

    it('adds the package to the sequence', async () => {
        const props = renderEditor(samplePackage);
        await userEvent.click(screen.getByRole('button', { name: 'Add to Sequence' }));
        expect(props.onAddToSequence).toHaveBeenCalledWith(samplePackage);
    });

    it('shows Luminance as L and puts Delete package in the Image List header', () => {
        renderEditor(
            { ...samplePackage, exposure_requests: [{ frameType: FrameType.LIGHT, filter: 'Luminance' as FilterType, exposureSec: 60, count: 2 }] },
            { onDeletePackage: vi.fn() },
        );
        expect(within(screen.getByRole('table')).getByText('L')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Delete package' })).toBeInTheDocument();
        expect(screen.queryByText('No exposure rows yet. Add one with the Image Planner above.')).not.toBeInTheDocument();
    });

    it('says how to add a row when the package has none', () => {
        renderEditor({ ...samplePackage, exposure_requests: [] });
        expect(screen.getByText('No exposure rows yet. Add one with the Image Planner above.')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: 'Add to Sequence' })).toBeDisabled();
    });

    it('offers the configured filters by short name, SPEC included', async () => {
        const props = renderEditor(samplePackage);
        const select = screen.getByRole('combobox', { name: 'Filter' }) as HTMLSelectElement;
        expect([...select.options].map((option) => option.textContent)).toEqual(['L', 'SPEC']);
        await userEvent.selectOptions(select, 'Star Analyzer 200');
        await userEvent.click(screen.getByRole('button', { name: 'Add to List' }));
        expect(props.onAddExposure).toHaveBeenCalledWith(expect.objectContaining({ filter: 'Star Analyzer 200' }));
    });

    it('says where to set filters when none are configured', () => {
        configuredFilters.list = [];
        renderEditor(samplePackage);
        expect(screen.getByText('No filters set. Add them in Settings, Observatory, Filters.')).toBeInTheDocument();
        configuredFilters.list = ['Luminance', 'Star Analyzer 200'];
    });

    it('shows a refused save as an alert', () => {
        renderEditor(samplePackage, { errorMessage: 'Could not save the package: revision conflict' });
        expect(screen.getByRole('alert')).toHaveTextContent('revision conflict');
    });

    it('sends a test image when Ekos is not in control', async () => {
        availability.canCommand = true;
        vi.mocked(captureTestFrame).mockResolvedValue('job-1');
        renderEditor(samplePackage);
        await userEvent.click(screen.getByRole('button', { name: 'Test Image' }));
        expect(captureTestFrame).toHaveBeenCalled();
    });

    it('greys Test Image while Ekos controls the equipment and explains why', async () => {
        availability.canCommand = false;
        availability.reason = 'Equipment commands are off. Ekos controls the equipment.';
        vi.mocked(captureTestFrame).mockClear();
        renderEditor(samplePackage);
        const button = screen.getByRole('button', { name: 'Test Image' });
        expect(button).toBeDisabled();
        expect(button).toHaveAttribute('aria-disabled', 'true');
        await userEvent.click(button);
        expect(captureTestFrame).not.toHaveBeenCalled();
        await userEvent.click(screen.getByRole('button', { name: 'Why unavailable?' }));
        expect(screen.getByRole('note')).toHaveTextContent('Ekos controls the equipment');
        availability.canCommand = true;
    });
});
