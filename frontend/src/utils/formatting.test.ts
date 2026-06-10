import { describe, expect, it } from 'vitest';
import { formatToolVersion } from './formatting';

describe('formatToolVersion', () => {
    it('reduces the raw ffmpeg banner to product + version token', () => {
        expect(
            formatToolVersion(
                'ffmpeg version 2026-01-26-git-fe0813d6e2-full_build-www.gyan.dev Copyright (c) 2000-2026 the FFmpeg developers',
            ),
        ).toBe('ffmpeg 2026-01-26-git-fe0813d6e2');
    });

    it('keeps a release-style ffmpeg version intact', () => {
        expect(
            formatToolVersion('ffmpeg version 7.1 Copyright (c) 2000-2025 the FFmpeg developers'),
        ).toBe('ffmpeg 7.1');
    });

    it('trims the MakeMKV platform suffix', () => {
        expect(formatToolVersion('MakeMKV v1.18.3 win(x64-release)')).toBe('MakeMKV v1.18.3');
    });

    it('translates the probe-timeout marker instead of leaking it', () => {
        expect(formatToolVersion('MakeMKV (version probe timed out)')).toBe(
            'Detected (version unknown)',
        );
    });

    it('falls back to "Detected" for a missing version', () => {
        expect(formatToolVersion(null)).toBe('Detected');
        expect(formatToolVersion('')).toBe('Detected');
    });

    it('truncates unrecognized long strings', () => {
        const long = 'x'.repeat(80);
        const out = formatToolVersion(long);
        expect(out.length).toBeLessThanOrEqual(60);
        expect(out.endsWith('…')).toBe(true);
    });
});
