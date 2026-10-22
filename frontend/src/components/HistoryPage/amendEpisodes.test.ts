import { describe, expect, it } from 'vitest';
import { amendEpisodeOptions } from './amendEpisodes';

// MXC S1 disc 2 (#770): titles t01..t06 matched S01E08..E13, t00 (really E07)
// was filed as an extra. The picker must offer E07.
const mxcTitles = [
  { matched_episode: 'extra' },
  ...[8, 9, 10, 11, 12, 13].map((n) => ({ matched_episode: `S01E${String(n).padStart(2, '0')}` })),
  { matched_episode: null },
];

const roster = (n: number) => ({
  available: true,
  season_number: 1,
  episodes: Array.from({ length: n }, (_, i) => ({
    episode_code: `S01E${String(i + 1).padStart(2, '0')}`,
    episode_number: i + 1,
    name: `Name ${i + 1}`,
  })),
});

describe('amendEpisodeOptions', () => {
  it('offers the whole season from the roster, not just the episodes siblings already claim (#770)', () => {
    const opts = amendEpisodeOptions(roster(13), mxcTitles, 1);
    expect(opts.map((o) => o.episode)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]);
    expect(opts.find((o) => o.episode === 7)?.name).toBe('Name 7');
  });

  it('falls back to a full numeric range when the roster is unavailable', () => {
    const opts = amendEpisodeOptions({ available: false, season_number: 1, episodes: [] }, mxcTitles, 1);
    expect(opts.map((o) => o.episode)).toContain(7);
    expect(opts[0].episode).toBe(1);
  });

  it('falls back while the roster is still loading', () => {
    expect(amendEpisodeOptions(null, mxcTitles, 1).map((o) => o.episode)).toContain(7);
  });

  it('never drops an episode a sibling claims, even past the roster or fallback range', () => {
    const titles = [{ matched_episode: 'S01E30-E31' }];
    expect(amendEpisodeOptions(roster(13), titles, 1).map((o) => o.episode)).toEqual(
      expect.arrayContaining([1, 13, 30, 31]),
    );
    expect(amendEpisodeOptions(null, titles, 1).map((o) => o.episode)).toEqual(
      expect.arrayContaining([1, 24, 30, 31]),
    );
  });

  it("ignores a roster for a different season than the amend's", () => {
    const opts = amendEpisodeOptions({ ...roster(3), season_number: 2 }, [], 1);
    expect(opts.length).toBeGreaterThan(3);
    expect(opts.every((o) => o.name === '')).toBe(true);
  });
});
