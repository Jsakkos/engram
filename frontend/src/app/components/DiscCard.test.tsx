import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DiscCard, type DiscData } from './DiscCard';

/** Minimal valid review_needed disc; override only what a test cares about. */
function makeDisc(overrides: Partial<DiscData> = {}): DiscData {
  return {
    id: '1',
    title: 'Frasier',
    subtitle: 'TV • FRASIER_S1D2',
    discLabel: 'FRASIER_S1D2',
    coverUrl: '/api/jobs/1/poster',
    mediaType: 'tv',
    state: 'review_needed',
    progress: 0,
    needsReview: true,
    tracks: [],
    tracksLoaded: true,
    ...overrides,
  };
}

beforeEach(() => {
  // usePosterImage fetches a poster on mount — stub it so the test stays
  // hermetic (jsdom has no real network) and logs nothing.
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false }));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('DiscCard — review affordances', () => {
  it('pre-rip review (no tracks): hides the review-queue button, keeps "Wrong title?", and shows the reason banner', () => {
    render(
      <DiscCard
        disc={makeDisc({
          tracks: [],
          reviewReason:
            '"Frasier" has multiple same-name shows on TMDB and the disc label has no year to tell them apart.',
        })}
        // App passes onReview=undefined when there are no tracks to review yet.
        onReview={undefined}
        onReIdentify={vi.fn()}
      />,
    );

    // The dead-end review-queue button is gone (the StateIndicator badge with
    // the same words is a non-button span, so this asserts the action button).
    expect(
      screen.queryByRole('button', { name: /review needed — open review queue/i }),
    ).not.toBeInTheDocument();

    // The corrective action remains...
    expect(
      screen.getByRole('button', { name: /wrong title — re-identify disc/i }),
    ).toBeInTheDocument();

    // ...and the card explains the ambiguity, pointing the user at "Wrong title?".
    expect(screen.getByText(/multiple same-name shows on TMDB/i)).toBeInTheDocument();
    expect(
      screen.getByText(/use "wrong title\?" to pick the correct one/i),
    ).toBeInTheDocument();
  });

  it('post-rip review (has tracks): shows the review-queue button and no banner', () => {
    render(
      <DiscCard
        disc={makeDisc({
          reviewReason: undefined,
          tracks: [
            { id: 't1', title: 'Title 0', duration: '22:14', state: 'review', progress: 0 },
          ],
        })}
        onReview={vi.fn()}
        onReIdentify={vi.fn()}
      />,
    );

    expect(
      screen.getByRole('button', { name: /review needed — open review queue/i }),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/use "wrong title\?" to pick the correct one/i),
    ).not.toBeInTheDocument();
  });

  it('titles not loaded yet: does not flash the pre-rip banner (title-load race)', () => {
    // A post-rip review_needed job whose titles haven't been fetched yet looks
    // identical to a pre-rip disc (tracks=[]). tracksLoaded=false must suppress
    // the banner so it doesn't flash on page load / WebSocket reconnect.
    render(
      <DiscCard
        disc={makeDisc({
          tracks: [],
          tracksLoaded: false,
          reviewReason: 'some pending reason',
        })}
        onReview={undefined}
        onReIdentify={vi.fn()}
      />,
    );

    expect(
      screen.queryByText(/use "wrong title\?" to pick the correct one/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/some pending reason/i)).not.toBeInTheDocument();
  });
});
