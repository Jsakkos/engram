import '@testing-library/jest-dom';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import NamePromptModal from './NamePromptModal';
import type { Job } from '../types';

const job: Job = {
    id: 3,
    drive_id: 'F:',
    volume_label: 'FIREFLY_S1_D2',
    content_type: 'tv',
    state: 'review_needed',
    current_speed: '',
    eta_seconds: 0,
    progress_percent: 0,
    current_title: 0,
    total_titles: 8,
    error_message: null,
};

function renderModal(overrides: Partial<Parameters<typeof NamePromptModal>[0]> = {}) {
    const props = {
        job,
        onSubmit: vi.fn(),
        onDismiss: vi.fn(),
        onCancelJob: vi.fn(),
        ...overrides,
    };
    render(<NamePromptModal {...props} />);
    return props;
}

describe('NamePromptModal dismissal vs cancellation', () => {
    it('Escape dismisses without cancelling the job', () => {
        const { onDismiss, onCancelJob } = renderModal();
        fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
        expect(onDismiss).toHaveBeenCalled();
        expect(onCancelJob).not.toHaveBeenCalled();
    });

    it('backdrop click dismisses without cancelling the job', () => {
        const { onDismiss, onCancelJob } = renderModal();
        fireEvent.click(screen.getByTestId('name-prompt-backdrop'));
        expect(onDismiss).toHaveBeenCalled();
        expect(onCancelJob).not.toHaveBeenCalled();
    });

    it('cancelling the job requires the explicit "Cancel job" button', () => {
        const { onDismiss, onCancelJob } = renderModal();
        fireEvent.click(screen.getByRole('button', { name: /cancel job/i }));
        expect(onCancelJob).toHaveBeenCalled();
        expect(onDismiss).not.toHaveBeenCalled();
    });

    it('submits trimmed title with content type and season', () => {
        const { onSubmit } = renderModal();
        fireEvent.change(screen.getByRole('textbox'), { target: { value: '  Firefly  ' } });
        fireEvent.click(screen.getByRole('button', { name: /save title/i }));
        expect(onSubmit).toHaveBeenCalledWith('Firefly', 'tv', 1);
    });
});

describe('NamePromptModal honest rip-first framing', () => {
    // The rip already started (walk-away rip-first) by the time this modal can
    // open, so the action saves the title — it does not "start" the rip.
    it('labels the primary action "Save title", never "Start Ripping"', () => {
        renderModal();
        expect(screen.getByRole('button', { name: /save title/i })).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: /start ripping/i })).not.toBeInTheDocument();
    });

    it('footer reads "Ripping" while the disc is still ripping (not "Awaiting Input")', () => {
        renderModal({ job: { ...job, state: 'ripping' } });
        expect(screen.getByText(/ripping/i)).toBeInTheDocument();
        expect(screen.queryByText(/awaiting input/i)).not.toBeInTheDocument();
    });

    it('footer reads "Awaiting Input" once the job is parked for review', () => {
        renderModal({ job: { ...job, state: 'review_needed' } });
        expect(screen.getByText(/awaiting input/i)).toBeInTheDocument();
    });

    it('surfaces the real identify reason instead of falsely claiming the label is unreadable', () => {
        const reason =
            'Could not find "Avatar: The Last Airbender Book One: Water" on TMDB. ' +
            'Please enter the correct show title.';
        renderModal({
            job: {
                ...job,
                volume_label: 'Avatar_Book_1_Disc_1',
                review_reason: reason,
            } as Job,
        });
        expect(screen.getByText(/could not find .* on tmdb/i)).toBeInTheDocument();
        expect(screen.queryByText(/cannot be read automatically/i)).not.toBeInTheDocument();
    });

    it('falls back to the unreadable-label message when no specific reason is given', () => {
        renderModal({ job: { ...job, review_reason: undefined } as Job });
        expect(screen.getByText(/cannot be read automatically/i)).toBeInTheDocument();
    });
});
