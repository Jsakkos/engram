import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Inspector } from './Inspector';
import type { DiscTitle } from '../../types';
import type { LLMFeedback } from './llmFeedback';

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
    onAssign?: (code: string) => void;
} = {}) {
    const onAssign = props.onAssign ?? vi.fn();
    render(
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
            onAssign={onAssign}
            onAction={vi.fn()}
            onRematch={vi.fn()}
            onDeepRematch={vi.fn()}
            onTryLLMMatch={vi.fn()}
            onAcceptLLMSuggestion={vi.fn()}
        />,
    );
    return { onAssign };
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

describe('Inspector — manual picker season (#370)', () => {
    // With no roster the picker falls back to a plain numbered list; the codes it
    // emits must carry the effective season, not a hardcoded S01.
    function pickFirstEpisode(season: number) {
        const { onAssign } = renderInspector({ season });
        const input = screen.getByLabelText('Manual episode for title 1');
        fireEvent.change(input, { target: { value: '1' } });
        fireEvent.keyDown(input, { key: 'Enter' });
        return onAssign;
    }

    it('generates fallback episode codes for the provided season, not S01', () => {
        expect(pickFirstEpisode(3)).toHaveBeenCalledWith('S03E01');
    });

    it('defaults to season 1 codes when season is 1', () => {
        expect(pickFirstEpisode(1)).toHaveBeenCalledWith('S01E01');
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
