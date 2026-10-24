import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Inspector } from './Inspector';
import type { DiscTitle } from '../../types';
import type { LLMFeedback } from './llmFeedback';
import type { RosterEpisode } from './types';

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
    selection?: string;
    episodes?: RosterEpisode[];
    holders?: Map<string, number[]>;
    onAssign?: (code: string) => void;
} = {}) {
    return render(
        <Inspector
            title={props.title ?? makeTitle()}
            candidates={[]}
            suggestion={null}
            selection={props.selection}
            action={undefined}
            episodes={props.episodes ?? []}
            season={props.season ?? 1}
            coverage={{}}
            holders={props.holders ?? new Map()}
            titleIndexById={{ 1: 1, 2: 2 }}
            isRematching={false}
            aiEpisodeMatchingEnabled={props.aiEpisodeMatchingEnabled ?? true}
            aiKeyConfigured={props.aiKeyConfigured ?? true}
            llmFeedback={props.llmFeedback ?? null}
            isLlmMatching={props.isLlmMatching ?? false}
            onAssign={props.onAssign ?? vi.fn()}
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
    // Behaviour of the controls themselves is covered in PlayerControls.test.tsx;
    // this only pins that the TV inspector still mounts them.
    it('renders the controls when the track has a ripped file', () => {
        renderInspector({
            title: makeTitle({ output_filename: 'C:\\staging\\title_01.mkv' }),
        });

        expect(screen.getByRole('link', { name: /open in player/i })).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /copy stream url/i })).toBeInTheDocument();
    });

    it('hides the controls when the track has no file at all', () => {
        renderInspector({
            title: makeTitle({ output_filename: null, organized_to: null }),
        });

        expect(screen.queryByRole('link', { name: /open in player/i })).not.toBeInTheDocument();
    });
});

describe('Inspector: review reason notice', () => {
    it('shows why the matcher could not match the track', () => {
        const message =
            'Only 1 reference subtitle is available for this season, too few to tell its episodes apart, so this track could not be matched by its dialogue. Assign the episode by hand.';
        renderInspector({
            title: makeTitle({
                match_details: JSON.stringify({ error: 'references_unreadable', message }),
            }),
        });
        expect(
            screen.getByText((content) => content.includes('too few to tell its episodes apart')),
        ).toBeInTheDocument();
    });

    it('shows the multi-episode explanation', () => {
        const message = 'This track appears to contain 3 episodes (S01E01, S01E02, S01E03).';
        renderInspector({
            title: makeTitle({
                match_details: JSON.stringify({ error: 'multi_episode_detected', message }),
            }),
        });
        expect(
            screen.getByText((content) => content.includes('appears to contain 3 episodes')),
        ).toBeInTheDocument();
    });

    it('shows why a half-hour-numbered match was held', () => {
        const message =
            'The reference subtitles for this season are numbered differently from TMDB (usually by broadcast half-hour instead of by segment), so S02E06 may name the wrong episode. Check it and assign the TMDB episode(s) this track holds.';
        renderInspector({
            title: makeTitle({
                match_details: JSON.stringify({ error: 'numbering_divergent', message }),
            }),
        });
        expect(
            screen.getByText((content) => content.includes('numbered differently from TMDB')),
        ).toBeInTheDocument();
    });

    it('shows why a non-English track was not matched', () => {
        const message =
            "This track's only audio is Japanese, and Engram matches episodes by English dialogue, so it could not be matched automatically. Assign the episode by hand.";
        renderInspector({
            title: makeTitle({
                match_details: JSON.stringify({
                    error: 'audio_language_unsupported',
                    audio_languages: ['ja'],
                    message,
                }),
            }),
        });
        expect(
            screen.getByText((content) => content.includes('only audio is Japanese')),
        ).toBeInTheDocument();
    });

    it('adds no notice when there is no message to show', () => {
        const plain = renderInspector({ title: makeTitle() });
        const baseline = plain.container.querySelectorAll('[data-testid="sv-notice"]').length;
        plain.unmount();
        const withCodeOnly = renderInspector({
            title: makeTitle({ match_details: JSON.stringify({ error: 'references_unreadable' }) }),
        });
        expect(withCodeOnly.container.querySelectorAll('[data-testid="sv-notice"]').length).toBe(
            baseline,
        );
    });

    it('does not repeat the file-exists message', () => {
        const message = 'S01E02 is already organized by another track on this disc.';
        renderInspector({
            title: makeTitle({ match_details: JSON.stringify({ error: 'file_exists', message }) }),
        });
        expect(screen.getAllByText((content) => content.includes(message))).toHaveLength(1);
    });

    it('leaves damaged-rip messages to the damaged track notice', () => {
        const plain = renderInspector({ title: makeTitle() });
        const baseline = plain.container.querySelectorAll('[data-testid="sv-notice"]').length;
        plain.unmount();
        const damaged = renderInspector({
            title: makeTitle({
                match_details: JSON.stringify({
                    error: 'incomplete_rip',
                    message: 'The rip of this track stopped early.',
                }),
            }),
        });
        expect(damaged.container.querySelectorAll('[data-testid="sv-notice"]').length).toBe(
            baseline,
        );
    });

    it('does not show raw matching failure text', () => {
        renderInspector({
            title: makeTitle({
                match_details: JSON.stringify({
                    error: 'matching_task_failed',
                    message: 'list index out of range',
                }),
            }),
        });
        expect(
            screen.queryByText((content) => content.includes('list index out of range')),
        ).not.toBeInTheDocument();
    });
});

describe('Inspector: manual multi-episode assignment (#830)', () => {
    const roster: RosterEpisode[] = [1, 2, 3, 4].map((n) => ({
        episode_code: `S01E0${n}`,
        episode_number: n,
        name: `Part ${n}`,
    }));
    const manual = () => screen.getByRole('combobox', { name: /manual episode/i });
    const alsoCovers = () => screen.getByRole('combobox', { name: /also covers/i });

    it('offers no "also covers" until an episode is picked', () => {
        renderInspector({ episodes: roster });
        expect(screen.queryByRole('combobox', { name: /also covers/i })).not.toBeInTheDocument();
    });

    it('shows a combined selection in both pickers instead of a blank', () => {
        renderInspector({ episodes: roster, selection: 'S01E01-E02' });
        expect(manual()).toHaveValue('S01E01');
        expect(alsoCovers()).toHaveValue('2');
    });

    it('extends a single pick into a canonical range', () => {
        const onAssign = vi.fn();
        renderInspector({ episodes: roster, selection: 'S01E01', onAssign });
        expect(alsoCovers()).toHaveValue('');
        fireEvent.change(alsoCovers(), { target: { value: '3' } });
        expect(onAssign).toHaveBeenCalledWith('S01E01-E03');
    });

    it('only offers later episodes, so a range can never run backwards', () => {
        renderInspector({ episodes: roster, selection: 'S01E02' });
        const values = Array.from(alsoCovers().querySelectorAll('option')).map((o) => o.value);
        expect(values).toEqual(['', '3', '4']);
    });

    it('narrows back to one episode', () => {
        const onAssign = vi.fn();
        renderInspector({ episodes: roster, selection: 'S01E01-E03', onAssign });
        fireEvent.change(alsoCovers(), { target: { value: '' } });
        expect(onAssign).toHaveBeenCalledWith('S01E01');
    });

    it('resets to a single episode when the first episode changes', () => {
        const onAssign = vi.fn();
        renderInspector({ episodes: roster, selection: 'S01E01-E02', onAssign });
        fireEvent.change(manual(), { target: { value: 'S01E03' } });
        expect(onAssign).toHaveBeenCalledWith('S01E03');
    });

    it('marks an episode another track already holds', () => {
        renderInspector({
            episodes: roster,
            selection: 'S01E01',
            holders: new Map([['S01E03', [2]]]),
        });
        expect(screen.getByRole('option', { name: /through E03.*#2/ })).toBeInTheDocument();
    });

    it('falls back to generated codes for the season when there is no roster', () => {
        const onAssign = vi.fn();
        renderInspector({ season: 2, selection: 'S02E05', onAssign });
        expect(screen.getByRole('option', { name: 'through S02E06' })).toBeInTheDocument();
        fireEvent.change(alsoCovers(), { target: { value: '6' } });
        expect(onAssign).toHaveBeenCalledWith('S02E05-E06');
    });

    it('keeps a gapped matcher pick visible without rewriting it', () => {
        renderInspector({ episodes: roster, selection: 'S01E01E03' });
        expect(manual()).toHaveValue('S01E01');
        expect(alsoCovers()).toHaveDisplayValue(/S01E01E03/);
    });
});
