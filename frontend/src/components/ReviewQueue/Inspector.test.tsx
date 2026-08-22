import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { toast } from 'sonner';
import { Inspector } from './Inspector';
import type { DiscTitle } from '../../types';
import type { LLMFeedback } from './llmFeedback';

vi.mock('sonner', () => ({
    toast: { success: vi.fn(), error: vi.fn() },
}));

function makeTitle(overrides: Partial<DiscTitle> = {}): DiscTitle {
    return {
        id: 1,
        job_id: 1,
        title_index: 1,
        duration_seconds: 1320,
        file_size_bytes: 1_000_000,
        chapter_count: 5,
        is_selected: true,
        output_filename: null,
        matched_episode: null,
        match_confidence: 0,
        state: 'review',
        ...overrides,
    };
}

function renderInspector(props: {
    title?: DiscTitle;
    llmFeedback?: LLMFeedback | null;
    isLlmMatching?: boolean;
    aiEpisodeMatchingEnabled?: boolean;
    aiKeyConfigured?: boolean;
    season?: number;
} = {}) {
    return render(
        <Inspector
            title={props.title ?? makeTitle()}
            candidates={[]}
            suggestion={null}
            selection={undefined}
            action={undefined}
            episodes={[]}
            season={props.season ?? 1}
            coverage={{}}
            holders={new Map()}
            titleIndexById={{ 1: 1 }}
            isRematching={false}
            aiEpisodeMatchingEnabled={props.aiEpisodeMatchingEnabled ?? true}
            aiKeyConfigured={props.aiKeyConfigured ?? true}
            llmFeedback={props.llmFeedback ?? null}
            isLlmMatching={props.isLlmMatching ?? false}
            onAssign={vi.fn()}
            onAction={vi.fn()}
            onRematch={vi.fn()}
            onDeepRematch={vi.fn()}
            onTryLLMMatch={vi.fn()}
            onAcceptLLMSuggestion={vi.fn()}
        />,
    );
}

describe('Inspector — AI match feedback', () => {
    it('shows a notice when llmFeedback is set and there is no suggestion', () => {
        renderInspector({ llmFeedback: { tone: 'warn', text: 'No confident AI match found.' } });
        expect(screen.getByText(/No confident AI match found\./)).toBeInTheDocument();
    });

    it('shows no notice when there is no feedback', () => {
        renderInspector({ llmFeedback: null });
        expect(screen.queryByText(/No confident AI match found\./)).not.toBeInTheDocument();
    });

    it('disables the button and shows Matching… while in flight', () => {
        renderInspector({ isLlmMatching: true });
        const btn = screen.getByRole('button', { name: /matching/i });
        expect(btn).toBeDisabled();
    });

    it('shows the default Try AI match label when idle', () => {
        renderInspector({ isLlmMatching: false });
        expect(screen.getByRole('button', { name: /try ai match/i })).toBeInTheDocument();
    });

    it('disables the button when no AI key is configured', () => {
        renderInspector({ aiKeyConfigured: false });
        const btn = screen.getByRole('button', { name: /try ai match/i });
        expect(btn).toBeDisabled();
        expect(btn).toHaveAttribute('title', expect.stringMatching(/no ai api key/i));
    });

    it('enables the button when matching is on and a key is set', () => {
        renderInspector({ aiKeyConfigured: true, isLlmMatching: false });
        expect(screen.getByRole('button', { name: /try ai match/i })).toBeEnabled();
    });

    it('hides the feedback notice when a suggestion is present', () => {
        const title = makeTitle({
            match_details: JSON.stringify({
                llm_suggestion: { episode: 3, confidence: 0.9, reasoning: 'ok', runner_up: null },
            }),
        });
        renderInspector({
            title,
            llmFeedback: { tone: 'warn', text: 'No confident AI match found.' },
        });
        // The suggestion card renders instead of the feedback notice.
        expect(screen.getByText(/Suggested:/)).toBeInTheDocument();
        expect(screen.queryByText(/No confident AI match found\./)).not.toBeInTheDocument();
    });
});

describe('Inspector — manual dropdown season (#370)', () => {
    it('generates fallback episode codes for the provided season, not S01', () => {
        renderInspector({ season: 3 });
        expect(screen.getByRole('option', { name: 'S03E01' })).toBeInTheDocument();
        expect(screen.queryByRole('option', { name: 'S01E01' })).not.toBeInTheDocument();
    });

    it('defaults to season 1 codes when season is 1', () => {
        renderInspector({ season: 1 });
        expect(screen.getByRole('option', { name: 'S01E01' })).toBeInTheDocument();
    });
});

describe('Inspector: organize failure (#563)', () => {
    const permissionError = "[Errno 13] Permission denied: '/library/tv'";

    it('shows the real cause when a file could not be moved', () => {
        renderInspector({
            title: makeTitle({
                match_details: JSON.stringify({
                    error: 'organize_failed',
                    message: permissionError,
                }),
            }),
        });
        expect(
            screen.getByText((content) => content.includes(permissionError)),
        ).toBeInTheDocument();
    });

    it('badges the track as a save failure, not as needing review', () => {
        renderInspector({
            title: makeTitle({
                match_details: JSON.stringify({
                    error: 'organize_failed',
                    message: permissionError,
                }),
            }),
        });
        expect(screen.getByText('Save failed')).toBeInTheDocument();
        expect(screen.queryByText('Needs review')).not.toBeInTheDocument();
    });

    it('leaves an ordinary unmatched track alone', () => {
        renderInspector({ title: makeTitle() });
        expect(screen.queryByText('Save failed')).not.toBeInTheDocument();
        expect(screen.getByText('Needs review')).toBeInTheDocument();
    });
});

describe('Inspector — external player controls', () => {
    it('renders both controls when the track has a ripped file', () => {
        renderInspector({
            title: makeTitle({ output_filename: 'C:\\staging\\title_01.mkv' }),
        });

        expect(screen.getByRole('link', { name: /open in player/i })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /copy stream url/i })).toBeInTheDocument();
    });

    it('renders the controls for an organized track with no staging copy', () => {
        renderInspector({
            title: makeTitle({
                output_filename: null,
                organized_to: 'C:\\tv\\Show\\Season 01\\Show - S01E01.mkv',
            }),
        });

        expect(screen.getByRole('link', { name: /open in player/i })).toBeInTheDocument();
    });

    it('hides the controls when the track has no file at all', () => {
        renderInspector({
            title: makeTitle({ output_filename: null, organized_to: null }),
        });

        expect(screen.queryByRole('link', { name: /open in player/i })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /copy stream url/i })).not.toBeInTheDocument();
    });

    it('points the player link at the playlist endpoint for this job and title', () => {
        renderInspector({
            title: makeTitle({
                id: 42,
                job_id: 7,
                output_filename: 'C:\\staging\\title_01.mkv',
            }),
        });

        const link = screen.getByRole('link', { name: /open in player/i });
        expect(link).toHaveAttribute('href', '/api/jobs/7/titles/42/playlist.m3u');
    });

    it('copies an absolute media URL to the clipboard', async () => {
        const writeText = vi.fn().mockResolvedValue(undefined);
        const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
        Object.assign(navigator, { clipboard: { writeText } });

        try {
            renderInspector({
                title: makeTitle({
                    id: 42,
                    job_id: 7,
                    output_filename: 'C:\\staging\\title_01.mkv',
                }),
            });

            await userEvent.click(screen.getByRole('button', { name: /copy stream url/i }));

            expect(writeText).toHaveBeenCalledWith(
                `${window.location.origin}/api/jobs/7/titles/42/media`,
            );
        } finally {
            if (originalClipboard) {
                Object.defineProperty(navigator, 'clipboard', originalClipboard);
            } else {
                delete (navigator as { clipboard?: unknown }).clipboard;
            }
        }
    });

    it('shows an error toast with the URL when the clipboard is unavailable', async () => {
        // Real-world target case: plain-HTTP LAN access is a non-secure context,
        // where `navigator.clipboard` is undefined and the call throws
        // synchronously. The copy control must never fail silently.
        const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
        delete (navigator as { clipboard?: unknown }).clipboard;

        try {
            renderInspector({
                title: makeTitle({
                    id: 42,
                    job_id: 7,
                    output_filename: 'C:\\staging\\title_01.mkv',
                }),
            });

            await userEvent.click(screen.getByRole('button', { name: /copy stream url/i }));

            expect(toast.error).toHaveBeenCalledWith(
                expect.stringContaining(`${window.location.origin}/api/jobs/7/titles/42/media`),
            );
        } finally {
            if (originalClipboard) {
                Object.defineProperty(navigator, 'clipboard', originalClipboard);
            } else {
                delete (navigator as { clipboard?: unknown }).clipboard;
            }
        }
    });

    it('shows the standing hint whenever the controls render', () => {
        // The hint is permanent, not error-triggered: a failed .m3u handoff
        // gives the page no signal to react to.
        renderInspector({
            title: makeTitle({ output_filename: 'C:\\staging\\title_01.mkv' }),
        });

        expect(screen.getByText(/open network stream/i)).toBeInTheDocument();
    });
});
