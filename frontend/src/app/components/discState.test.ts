import { describe, expect, it } from 'vitest';
import { DISC_STATE_CONFIG, discStateLabel, isActivePipelineState } from './discState';
import { IcoMatching } from './icons';

describe('disc state config', () => {
    it('gives ORGANIZING its own icon instead of reusing the matching glyph', () => {
        expect(DISC_STATE_CONFIG.organizing.icon).not.toBe(IcoMatching);
        expect(DISC_STATE_CONFIG.organizing.icon).toBe(DISC_STATE_CONFIG.backing_up.icon);
    });

    it('labels the whole-disc backup phase', () => {
        expect(discStateLabel('backing_up')).toBe('BACKING UP');
    });

    it('counts backing_up as an active pipeline state', () => {
        expect(isActivePipelineState('backing_up')).toBe(true);
    });

    it('formats labels from the shared config', () => {
        expect(discStateLabel('organizing')).toBe('ORGANIZING');
        expect(discStateLabel('review_needed')).toBe('REVIEW NEEDED');
    });
});
