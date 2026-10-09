import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { OrderingSelector } from './OrderingSelector';
import { effectiveOrdering } from './utils';
import type { OrderingOption } from './types';

const AIRED: OrderingOption = { ordering: 'aired', label: 'Aired Order', tmdb_type: 1, diverges: false, projection: {} };
const DVD: OrderingOption = { ordering: 'dvd', label: 'DVD Order', tmdb_type: 3, diverges: true, projection: {} };
const TVDB: OrderingOption = { ordering: 'tvdb', label: 'TheTVDB', tmdb_type: 0, diverges: true, projection: {} };

describe('OrderingSelector', () => {
    it('shows the TheTVDB attribution link only when a tvdb option exists', () => {
        const { unmount } = render(<OrderingSelector options={[AIRED, DVD]} current="aired" onChange={vi.fn()} />);
        expect(screen.queryByRole('link', { name: /TheTVDB/ })).toBeNull();
        unmount();
        render(<OrderingSelector options={[AIRED, TVDB]} current="aired" onChange={vi.fn()} />);
        expect(screen.getByRole('link', { name: /TheTVDB/ })).toBeTruthy();
    });

    it('selecting TheTVDB in the fallback state retries the switch', async () => {
        // The show prefers TheTVDB but this job fell back to TMDB during an
        // outage: the effective current is "aired", so TheTVDB is clickable.
        const current = effectiveOrdering({ current_ordering: 'tvdb', episode_source: 'tmdb' });
        expect(current).toBe('aired');
        const onChange = vi.fn();
        render(<OrderingSelector options={[AIRED, TVDB]} current={current} onChange={onChange} />);
        await userEvent.click(screen.getByRole('button', { name: /TheTVDB/ }));
        expect(onChange).toHaveBeenCalledWith('tvdb');
    });

    it('selecting the already-active TheTVDB ordering is a no-op', async () => {
        const current = effectiveOrdering({ current_ordering: 'tvdb', episode_source: 'tvdb' });
        expect(current).toBe('tvdb');
        const onChange = vi.fn();
        render(<OrderingSelector options={[AIRED, TVDB]} current={current} onChange={onChange} />);
        await userEvent.click(screen.getByRole('button', { name: /TheTVDB/ }));
        expect(onChange).not.toHaveBeenCalled();
    });
});

describe('effectiveOrdering', () => {
    it('defaults to aired and passes TMDB orderings through', () => {
        expect(effectiveOrdering({})).toBe('aired');
        expect(effectiveOrdering({ current_ordering: 'dvd', episode_source: 'tmdb' })).toBe('dvd');
    });
});
