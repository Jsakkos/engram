import '@testing-library/jest-dom';
import { render, screen, fireEvent } from '@testing-library/react';
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
  it('identity review (no tracks): hides the review-queue button, keeps "Wrong title?", and shows the reason banner', () => {
    render(
      <DiscCard
        disc={makeDisc({
          identityReview: true,
          tracks: [],
          reviewReason:
            '"Frasier" has multiple same-name shows on TMDB and the disc label has no year to tell them apart.',
        })}
        // App passes onReview=undefined for an identity review.
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
      screen.getByText(/use "wrong title\?" to pick the correct show/i),
    ).toBeInTheDocument();
  });

  it('identity review WITH enumerated (pending) tracks: still hides the button + shows the banner', () => {
    // The real ambiguous-disc case (e.g. The Office): titles are enumerated at
    // scan time, so the disc has tracks even before ripping. The old tracks===0
    // gate never fired here; identityReview must drive the declutter regardless.
    render(
      <DiscCard
        disc={makeDisc({
          identityReview: true,
          tracks: [
            { id: 't1', title: 'Title 0', duration: '22:14', state: 'pending', progress: 0 },
            { id: 't2', title: 'Title 1', duration: '21:58', state: 'pending', progress: 0 },
          ],
          reviewReason: '"The Office" has multiple same-name shows on TMDB. Pick the correct one.',
        })}
        onReview={undefined}
        onReIdentify={vi.fn()}
      />,
    );

    expect(
      screen.queryByRole('button', { name: /review needed — open review queue/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText(/use "wrong title\?" to pick the correct show/i),
    ).toBeInTheDocument();
  });

  it('episode review (identity confirmed, has review tracks): shows the review-queue button and no banner', () => {
    render(
      <DiscCard
        disc={makeDisc({
          identityReview: false,
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
      screen.queryByText(/use "wrong title\?" to pick the correct show/i),
    ).not.toBeInTheDocument();
  });

  it('titles not loaded yet: does not flash the identity-review banner (title-load race)', () => {
    // A post-rip review_needed job whose titles haven't been fetched yet can have
    // identityReview=true transiently (the adapter sees titles=[]). tracksLoaded=false
    // must suppress the banner so it doesn't flash on page load / WebSocket reconnect.
    render(
      <DiscCard
        disc={makeDisc({
          identityReview: true,
          tracks: [],
          tracksLoaded: false,
          reviewReason: 'some pending reason',
        })}
        onReview={undefined}
        onReIdentify={vi.fn()}
      />,
    );

    expect(
      screen.queryByText(/use "wrong title\?" to pick the correct show/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/some pending reason/i)).not.toBeInTheDocument();
  });
});

describe('DiscCard — identify CTA (P13 non-modal affordance)', () => {
  it('renders a labelled CTA that fires onIdentify when provided', () => {
    const onIdentify = vi.fn();
    render(
      <DiscCard
        disc={makeDisc({ identityReview: true, tracks: [] })}
        onIdentify={onIdentify}
        identifyLabel="Name this disc"
      />,
    );

    const cta = screen.getByTestId('disccard-identify-cta');
    expect(cta).toHaveTextContent(/name this disc/i);
    fireEvent.click(cta);
    expect(onIdentify).toHaveBeenCalledTimes(1);
  });

  it('renders no CTA when onIdentify is not provided', () => {
    render(<DiscCard disc={makeDisc({ identityReview: true, tracks: [] })} />);
    expect(screen.queryByTestId('disccard-identify-cta')).not.toBeInTheDocument();
  });
});

describe('DiscCard — TMDB degraded-mode alert (#243)', () => {
  it('renders the per-job degraded reason verbatim on an active job', () => {
    // The per-job reason (set by the backend when the key was absent/rejected at
    // classification time) must win over the global boolean: it also covers the
    // configured-but-invalid key case the global flag cannot see.
    render(
      <DiscCard
        disc={makeDisc({
          state: 'matching',
          needsReview: false,
          tmdbDegradedReason:
            'TMDB rejected the configured API key — classification ran in heuristic-only mode.',
        })}
        onReview={vi.fn()}
        onReIdentify={vi.fn()}
      />,
    );

    expect(screen.getByText(/TMDB rejected the configured API key/i)).toBeInTheDocument();
  });

  it('suppresses the generic not-configured warning — the global banner owns it', () => {
    // With no per-job reason, repeating "TMDB not configured" on every card
    // just duplicates the dashboard-level banner N times on one screen.
    render(
      <DiscCard
        disc={makeDisc({ state: 'matching', needsReview: false })}
        onReview={vi.fn()}
        onReIdentify={vi.fn()}
      />,
    );

    expect(screen.queryByText(/TMDB not configured/i)).not.toBeInTheDocument();
  });

  it('shows no degraded alert on completed jobs (keeps done cards clean)', () => {
    render(
      <DiscCard
        disc={makeDisc({
          state: 'completed',
          needsReview: false,
          tmdbDegradedReason: 'TMDB API key not configured — classification ran in heuristic-only mode.',
        })}
        onReview={vi.fn()}
        onReIdentify={vi.fn()}
      />,
    );

    expect(screen.queryByText(/heuristic-only/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/TMDB not configured/i)).not.toBeInTheDocument();
  });
});

describe('DiscCard — organizing', () => {
  it('TV multi-track: shows a count-based progress bar (N of M organized)', () => {
    render(
      <DiscCard
        disc={makeDisc({
          state: 'organizing',
          mediaType: 'tv',
          needsReview: false,
          tracks: [
            { id: 't0', title: 'S01E01', duration: '22:00', state: 'completed', progress: 100, organizedTo: '/tv/Show/S01E01.mkv' },
            { id: 't1', title: 'S01E02', duration: '22:00', state: 'completed', progress: 100, organizedTo: '/tv/Show/S01E02.mkv' },
            { id: 't2', title: 'S01E03', duration: '22:00', state: 'matched', progress: 100 },
            { id: 't3', title: 'S01E04', duration: '22:00', state: 'matched', progress: 100 },
          ],
        })}
        onReview={vi.fn()}
        onReIdentify={vi.fn()}
      />,
    );

    // 2 of 4 organized → a real 50% bar (not the indeterminate pulse).
    const bar = screen.getByTestId('sv-bar-progress');
    expect(bar).toHaveAttribute('data-value', '50');
  });

  it('single-file movie: shows an indeterminate pulse, not a 0/1 bar', () => {
    render(
      <DiscCard
        disc={makeDisc({
          state: 'organizing',
          mediaType: 'movie',
          title: 'Inception',
          needsReview: false,
          tracks: [
            { id: 't0', title: 'Main Feature', duration: '2:28:00', state: 'matched', progress: 100 },
          ],
        })}
        onReview={vi.fn()}
        onReIdentify={vi.fn()}
      />,
    );

    expect(screen.queryByTestId('sv-bar-progress')).not.toBeInTheDocument();
    expect(screen.getByText(/ORGANIZING TO LIBRARY/i)).toBeInTheDocument();
  });
});

describe('DiscCard — cancel confirmation', () => {
  it('clicking cancel opens the confirmation modal without firing onCancel', () => {
    const onCancel = vi.fn();
    const { container } = render(
      <DiscCard
        disc={makeDisc({ state: 'ripping' })}
        onCancel={onCancel}
      />,
    );

    // Hover the card to reveal the cancel button
    fireEvent.mouseEnter(container.firstChild as Element);
    fireEvent.click(screen.getByRole('button', { name: /cancel job/i }));

    expect(screen.getByText(/cancel rip\?/i)).toBeInTheDocument();
    expect(onCancel).not.toHaveBeenCalled();
  });

  it('confirming the modal fires onCancel; dismissing does not', () => {
    const onCancel = vi.fn();
    const { container } = render(
      <DiscCard
        disc={makeDisc({ state: 'ripping' })}
        onCancel={onCancel}
      />,
    );

    fireEvent.mouseEnter(container.firstChild as Element);
    fireEvent.click(screen.getByRole('button', { name: /cancel job/i }));
    fireEvent.click(screen.getByRole('button', { name: /keep ripping/i }));
    expect(onCancel).not.toHaveBeenCalled();

    // Reopen and confirm
    fireEvent.click(screen.getByRole('button', { name: /cancel job/i }));
    fireEvent.click(screen.getByRole('button', { name: /^cancel rip$/i }));
    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});

describe('DiscCard — manual identity provenance', () => {
  it('shows the MANUAL ID chip when identity was asserted before ripping', () => {
    render(<DiscCard disc={makeDisc({ identitySource: 'manual' })} />);
    expect(screen.getByText(/manual id/i)).toBeInTheDocument();
  });

  it('shows the chip for a corrected identity too', () => {
    render(<DiscCard disc={makeDisc({ identitySource: 'manual_correction' })} />);
    expect(screen.getByText(/manual id/i)).toBeInTheDocument();
  });

  it('shows no chip for an automatically identified disc', () => {
    render(<DiscCard disc={makeDisc({ identitySource: 'tmdb' })} />);
    expect(screen.queryByText(/manual id/i)).not.toBeInTheDocument();
  });

  it('labels the identity button "Edit ID" on a manual disc', () => {
    render(<DiscCard disc={makeDisc({ identitySource: 'manual' })} onReIdentify={vi.fn()} />);
    expect(screen.getByRole('button', { name: /edit id/i })).toBeInTheDocument();
  });

  it('labels it "Wrong title?" on an auto-identified disc', () => {
    render(<DiscCard disc={makeDisc({ identitySource: 'tmdb' })} onReIdentify={vi.fn()} />);
    expect(screen.getByRole('button', { name: /wrong title/i })).toBeInTheDocument();
  });
});
