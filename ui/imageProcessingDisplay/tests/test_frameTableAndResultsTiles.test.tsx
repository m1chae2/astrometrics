/**
 * @fileoverview Tests that the analysis results tiles and the file browser show only
 * numbers the backend sends: no frame-count blocks and no star width tiles or column that nothing fills.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { AnalysisResults } from '../analysisResults/AnalysisResults';
import { FileBrowser } from '../fileBrowser/FileBrowser';

describe('AnalysisResults', () => {
    it('shows no count blocks for photometry or spectroscopy, and keeps the spectroscopy note', () => {
        const { container, rerender } = render(
            <AnalysisResults results={{ status: 'completed', targetId: 'M 31', analysisMode: 'photometry', totalImages: 10, starsFound: 5, framesProcessed: 9 }} />
        );
        expect(container.firstChild).toBeNull();
        rerender(
            <AnalysisResults results={{ status: 'completed', targetId: 'M 31', analysisMode: 'spectroscopy', totalImages: 10, starsProcessed: 3, spectraExtracted: 2 }} />
        );
        for (const label of ['Total Images', 'Stars Found', 'Frames Processed', 'Stars Processed', 'Spectra Extracted']) {
            expect(screen.queryByText(label)).toBeNull();
        }
        expect(screen.getByText(/saved to the global StellarObject registry/)).toBeTruthy();
    });

    it('shows no total, unusual brightness or accepted blocks, and no job message', () => {
        render(
            <AnalysisResults
                results={{ status: 'completed', targetId: 'M 31', analysisMode: 'variability', totalImages: 10, rejectedCount: 2, message: 'Ran photometry.' }}
            />
        );
        expect(screen.queryByText('Ran photometry.')).toBeNull();
        expect(screen.queryByText('Total Images')).toBeNull();
        expect(screen.queryByText('Unusual brightness')).toBeNull();
        expect(screen.queryByText('Accepted')).toBeNull();
        expect(screen.queryByText(/FWHM/)).toBeNull();
    });
});

describe('FileBrowser', () => {
    it('has no star width column', () => {
        render(
            <FileBrowser
                files={[]}
                rejectedFiles={[]}
                onSelectFile={() => {}}
                selectedFile={null}
                searchTerm=""
                onSearchChange={() => {}}
                checkedFiles={new Set()}
                onToggleFile={() => {}}
                onToggleAll={() => {}}
                selectedCamera=""
            />
        );
        expect(screen.queryByText('FWHM')).toBeNull();
        expect(screen.getByText('Status')).toBeTruthy();
    });

    /** A frame the variable-star search left out reads "Unusual brightness" and says it is still stacked. */
    it('labels a left-out frame as unusual brightness, not rejected', () => {
        const file = {
            path: '/lib/M_31_Light_Luminance_119.fits',
            name: 'M_31_Light_Luminance_119.fits',
            camera: 'ZWO ASI 533MM Pro',
            gain: null,
            exposureS: 60,
            filter: 'L',
            date: '2026-11-04 21:09:58',
        };
        render(
            <FileBrowser
                files={[file]}
                rejectedFiles={[file.path]}
                onSelectFile={() => {}}
                selectedFile={null}
                searchTerm=""
                onSearchChange={() => {}}
                checkedFiles={new Set()}
                onToggleFile={() => {}}
                onToggleAll={() => {}}
                selectedCamera="All"
            />
        );
        const status = screen.getByText('Unusual brightness').closest('[title]');
        expect(status?.getAttribute('title')).toMatch(/still in the stack/);
        expect(screen.queryByText('Rejected')).toBeNull();
    });
});
