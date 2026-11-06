import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useSeasonRoster } from './useSeasonRoster';

const rosterFor = (showId: number) => ({
    available: true,
    season_number: 1,
    show_id: showId,
    episodes: [{ episode_code: 'S01E01', episode_number: 1, name: `Show ${showId} pilot` }],
    reason: null,
});

afterEach(() => vi.unstubAllGlobals());

describe('useSeasonRoster', () => {
    it("drops the previous job's roster as soon as the job changes (#770 review)", async () => {
        let resolveB: (v: unknown) => void = () => {};
        const fetchMock = vi.fn((url: string) =>
            url.includes('/jobs/1/')
                ? Promise.resolve({ ok: true, json: () => Promise.resolve(rosterFor(100)) })
                : new Promise((r) => {
                      resolveB = r;
                  }),
        );
        vi.stubGlobal('fetch', fetchMock);

        const { result, rerender } = renderHook(({ id }) => useSeasonRoster(id, 1), {
            initialProps: { id: '1' as string | undefined },
        });
        await waitFor(() => expect(result.current.roster?.show_id).toBe(100));

        // Same season number, different job: job 1's roster must not stand in for job 2's.
        rerender({ id: '2' });
        expect(result.current.roster).toBeNull();
        expect(result.current.loading).toBe(true);

        resolveB({ ok: true, json: () => Promise.resolve(rosterFor(200)) });
        await waitFor(() => expect(result.current.roster?.show_id).toBe(200));
    });

    it('drops the roster when there is no job to show it for', async () => {
        vi.stubGlobal(
            'fetch',
            vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve(rosterFor(100)) })),
        );
        const { result, rerender } = renderHook(({ id }) => useSeasonRoster(id, 1), {
            initialProps: { id: '1' as string | undefined },
        });
        await waitFor(() => expect(result.current.roster).not.toBeNull());
        rerender({ id: undefined });
        expect(result.current.roster).toBeNull();
    });
});
