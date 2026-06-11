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
function makeTitle(state: TitleState, id = 1): DiscTitle {
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
  };
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
