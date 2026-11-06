/**
 * @fileoverview Tests that the analysis results tiles and the file browser show only
 * numbers the backend sends: no star width tiles or column that nothing fills.
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { AnalysisResults } from '../analysisResults/AnalysisResults';
import { FileBrowser } from '../fileBrowser/FileBrowser';

describe('AnalysisResults', () => {
    it('shows the frame counts and no star width tile', () => {
        render(
            <AnalysisResults
                results={{ status: 'completed', targetId: 'M 31', analysisMode: 'variability', totalImages: 10, rejectedCount: 2 }}
            />
        );
        expect(screen.getByText('Accepted')).toBeTruthy();
        expect(screen.getByText('8')).toBeTruthy();
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
            iso: '',
            exposure: '60.0',
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
        const status = screen.getByText('Unusual brightness');
        expect(status.getAttribute('title')).toMatch(/still in the stack/);
        expect(screen.queryByText('Rejected')).toBeNull();
    });
});
