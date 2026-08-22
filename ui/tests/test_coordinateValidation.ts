/**
 * @fileoverview Tests for RA/Dec entry validation.
 */

import { describe, it, expect } from 'vitest';
import { validateRightAscension, validateDeclination } from '../common/utils/coordinateValidation';

describe('validateRightAscension', () => {
    it('accepts the sexagesimal forms the backend parses', () => {
        for (const value of ['09:55:33', '09 55 33', '09h55m33s', '9:55', '12']) {
            expect(validateRightAscension(value)).toBeNull();
        }
    });

    it('treats an empty value as not supplied', () => {
        expect(validateRightAscension('')).toBeNull();
        expect(validateRightAscension('   ')).toBeNull();
    });

    it('accepts the exact range boundaries', () => {
        expect(validateRightAscension('0')).toBeNull();
        expect(validateRightAscension('24')).toBeNull();
    });

    it('rejects hours beyond 24', () => {
        expect(validateRightAscension('24.5')).toMatch(/between 0 and 24/);
        expect(validateRightAscension('25:00:00')).toMatch(/between 0 and 24/);
    });

    it('rejects negative right ascension', () => {
        expect(validateRightAscension('-1:00:00')).toMatch(/between 0 and 24/);
    });

    it('rejects minutes or seconds of 60 and above', () => {
        expect(validateRightAscension('09:60:00')).not.toBeNull();
        expect(validateRightAscension('09:30:75')).not.toBeNull();
    });

    it('rejects non-numeric and over-long input', () => {
        expect(validateRightAscension('abc')).not.toBeNull();
        expect(validateRightAscension('1:2:3:4')).not.toBeNull();
    });
});

describe('validateDeclination', () => {
    it('accepts signed sexagesimal and decimal forms', () => {
        for (const value of ['+69:03:55', '-30 15 00', '69d03m55s', '-89.9', '0']) {
            expect(validateDeclination(value)).toBeNull();
        }
    });

    it('accepts the exact range boundaries', () => {
        expect(validateDeclination('90')).toBeNull();
        expect(validateDeclination('-90')).toBeNull();
        expect(validateDeclination('+90:00:00')).toBeNull();
    });

    it('rejects declinations outside +/-90', () => {
        expect(validateDeclination('91')).toMatch(/between -90 and \+90/);
        expect(validateDeclination('-90:00:01')).toMatch(/between -90 and \+90/);
    });

    it('applies the leading sign to the whole value, not just degrees', () => {
        // -30 15 00 is -30.25 deg, comfortably in range; the sign must not be
        // dropped or applied per-component (which would give -29.75).
        expect(validateDeclination('-30 15 00')).toBeNull();
        expect(validateDeclination('-89 45 00')).toBeNull();
    });

    it('rejects non-numeric input', () => {
        expect(validateDeclination('north')).not.toBeNull();
    });
});
