import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Inspector } from './Inspector';
import type { DiscTitle, Job } from '../../types';
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

function makeJob(overrides: Partial<Job> = {}): Job {
    return {
        id: 1,
        drive_id: 'E:',
        volume_label: 'SHOW_S1D1',
        content_type: 'tv',
        state: 'review_needed',
        current_speed: '',
        eta_seconds: 0,
        progress_percent: 0,
        current_title: 0,
        total_titles: 0,
        error_message: null,
        detected_title: 'Show',
        detected_season: 1,
        ...overrides,
    };
}

function renderInspector(props: {
    llmFeedback?: LLMFeedback | null;
    isLlmMatching?: boolean;
    aiEpisodeMatchingEnabled?: boolean;
} = {}) {
    return render(
        <Inspector
            title={makeTitle()}
            job={makeJob()}
            candidates={[]}
            suggestion={null}
            selection={undefined}
            action={undefined}
            episodes={[]}
            coverage={{}}
            holders={new Map()}
            titleIndexById={{ 1: 1 }}
            isRematching={false}
            aiEpisodeMatchingEnabled={props.aiEpisodeMatchingEnabled ?? true}
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
});
