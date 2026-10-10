import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { TvdbAttribution } from './TvdbAttribution';

describe('TvdbAttribution', () => {
    it('links to TheTVDB in a new tab', () => {
        render(<TvdbAttribution />);
        const link = screen.getByRole('link', { name: /TheTVDB/ });
        expect(link.getAttribute('href')).toBe('https://thetvdb.com');
        expect(link.getAttribute('target')).toBe('_blank');
        expect(link.getAttribute('rel')).toContain('noopener');
    });
});
