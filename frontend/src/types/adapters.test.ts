import { describe, expect, it } from 'vitest';
import { transformJobToDiscData } from './adapters';
import type { DiscTitle, Job, TitleState } from './index';

/** Minimal valid Job; override only the fields a test cares about. */
function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: 1,
    drive_id: 'E:',
    volume_label: 'THE_OFFICE_S2D1',
    content_type: 'tv',
    state: 'review_needed',
    current_speed: '',
    eta_seconds: 0,
    progress_percent: 0,
    current_title: 0,
    total_titles: 0,
    error_message: null,
    detected_title: 'The Office',
    detected_season: 2,
    ...overrides,
  };
}

/** Minimal valid DiscTitle in a given state. */
function makeTitle(state: TitleState, id = 1, overrides: Partial<DiscTitle> = {}): DiscTitle {
  return {
    id,
    job_id: 1,
    title_index: id,
    duration_seconds: 1320,
    file_size_bytes: 0,
    chapter_count: 6,
    is_selected: true,
    output_filename: null,
    matched_episode: null,
    match_confidence: 0,
    state,
    ...overrides,
  };
}

/** Build a matched DiscTitle with an episode code. */
function makeMatchedTitle(episode: string, id = 1, extra = false): DiscTitle {
  return makeTitle('completed', id, { matched_episode: episode, is_extra: extra });
}

const TWO_CANDIDATES = JSON.stringify([
  { tmdb_id: 18409, name: 'The Office', year: '2005', popularity: 250 },
  { tmdb_id: 17730, name: 'The Office', year: '2001', popularity: 84 },
]);

describe('transformJobToDiscData — identityReview derivation', () => {
  it('is true when tmdb_id is null (analyst withheld the id) even with enumerated pending tracks', () => {
    const disc = transformJobToDiscData(
      makeJob({ tmdb_id: null, candidates_json: TWO_CANDIDATES }),
      [makeTitle('pending', 1), makeTitle('pending', 2)],
    );
    expect(disc.identityReview).toBe(true);
  });

  it('is false when identity is confirmed (tmdb_id set) and titles are in review', () => {
    const disc = transformJobToDiscData(
      makeJob({ tmdb_id: 18409, tmdb_name: 'The Office', tmdb_year: 2005 }),
      [makeTitle('review', 1)],
    );
    expect(disc.identityReview).toBe(false);
  });

  it('is true for a same-name collision with a best-guess id but no ripped titles (no-year twin)', () => {
    const disc = transformJobToDiscData(
      makeJob({ tmdb_id: 18409, candidates_json: TWO_CANDIDATES }),
      [makeTitle('pending', 1), makeTitle('queued', 2)],
    );
    expect(disc.identityReview).toBe(true);
  });

  it('is false for a same-name collision once a title has been matched (post-rip wrong-show keeps its button)', () => {
    const disc = transformJobToDiscData(
      makeJob({ tmdb_id: 18409, candidates_json: TWO_CANDIDATES }),
      [makeTitle('matched', 1)],
    );
    expect(disc.identityReview).toBe(false);
  });

  it('is false when the job is not in review_needed', () => {
    const disc = transformJobToDiscData(
      makeJob({ state: 'matching', tmdb_id: null }),
      [makeTitle('matching', 1)],
    );
    expect(disc.identityReview).toBe(false);
  });

  it('passes tmdb identity fields through to DiscData', () => {
    const disc = transformJobToDiscData(
      makeJob({ tmdb_id: 18409, tmdb_name: 'The Office', tmdb_year: 2005 }),
      [],
    );
    expect(disc.tmdbId).toBe(18409);
    expect(disc.tmdbName).toBe('The Office');
    expect(disc.tmdbYear).toBe(2005);
  });
});

describe('transformJobToDiscData — promptKind derivation', () => {
  it("is 'name' for an unreadable label with no detected title", () => {
    const disc = transformJobToDiscData(
      makeJob({
        detected_title: undefined,
        review_reason: 'Disc label unreadable. Please enter the title to continue.',
      }),
      [],
    );
    expect(disc.promptKind).toBe('name');
  });

  it("is 'season' when the show is known but the season is not", () => {
    const disc = transformJobToDiscData(
      makeJob({ review_reason: 'Show identified — select a season to continue.' }),
      [],
    );
    expect(disc.promptKind).toBe('season');
  });

  it('is null when the job is not in review_needed', () => {
    const disc = transformJobToDiscData(
      makeJob({
        state: 'matching',
        detected_title: undefined,
        review_reason: 'Disc label unreadable. Please enter the title to continue.',
      }),
      [],
    );
    expect(disc.promptKind).toBeNull();
  });

  it('is null for a review job that needs no identify prompt', () => {
    const disc = transformJobToDiscData(
      makeJob({ review_reason: 'Low-confidence episode matches need review.' }),
      [],
    );
    expect(disc.promptKind).toBeNull();
  });
});

describe('transformJobToDiscData — subtitle enrichment for terminal states', () => {
  it('shows episode range for a completed TV job', () => {
    const disc = transformJobToDiscData(
      makeJob({ state: 'completed', content_type: 'tv' }),
      [
        makeMatchedTitle('S02E01', 1),
        makeMatchedTitle('S02E02', 2),
        makeMatchedTitle('S02E03', 3),
      ],
    );
    expect(disc.subtitle).toBe('TV · S02 E01–E03');
  });

  it('includes multi-season range when a disc spans two seasons', () => {
    const disc = transformJobToDiscData(
      makeJob({ state: 'completed', content_type: 'tv' }),
      [
        makeMatchedTitle('S01E08', 1),
        makeMatchedTitle('S02E01', 2),
      ],
    );
    expect(disc.subtitle).toBe('TV · S01 E08 · S02 E01');
  });

  it('excludes extras from the episode range', () => {
    const disc = transformJobToDiscData(
      makeJob({ state: 'completed', content_type: 'tv' }),
      [
        makeMatchedTitle('S02E01', 1),
        makeMatchedTitle('S02E02', 2),
        makeMatchedTitle('S02E03', 3, /* extra */ true),
      ],
    );
    expect(disc.subtitle).toBe('TV · S02 E01–E02');
  });

  it('falls back to disc label when no matched_episode codes are available', () => {
    const disc = transformJobToDiscData(
      makeJob({ state: 'completed', content_type: 'tv' }),
      [makeTitle('completed', 1)],
    );
    expect(disc.subtitle).toBe('TV · THE_OFFICE_S2D1');
  });

  it('shows year for a completed movie job with tmdb_year', () => {
    const disc = transformJobToDiscData(
      makeJob({ state: 'completed', content_type: 'movie', tmdb_year: 2010 }),
      [],
    );
    expect(disc.subtitle).toBe('Movie · 2010');
  });

  it('falls back to disc label for a movie without tmdb_year', () => {
    const disc = transformJobToDiscData(
      makeJob({ state: 'completed', content_type: 'movie', tmdb_year: undefined }),
      [],
    );
    expect(disc.subtitle).toMatch(/^Movie · /);
    expect(disc.subtitle).toContain('THE_OFFICE_S2D1');
  });

  it('shows enriched subtitle during organizing state too', () => {
    const disc = transformJobToDiscData(
      makeJob({ state: 'organizing', content_type: 'tv' }),
      [makeMatchedTitle('S01E01', 1), makeMatchedTitle('S01E02', 2)],
    );
    expect(disc.subtitle).toBe('TV · S01 E01–E02');
  });

  it('uses raw disc label subtitle for non-terminal states', () => {
    const disc = transformJobToDiscData(
      makeJob({ state: 'ripping', content_type: 'tv' }),
      [makeMatchedTitle('S02E01', 1)],
    );
    expect(disc.subtitle).toContain('THE_OFFICE_S2D1');
    expect(disc.subtitle).not.toContain('E01');
  });
});
