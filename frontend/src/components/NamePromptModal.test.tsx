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
        fireEvent.click(screen.getByRole('button', { name: /start ripping/i }));
        expect(onSubmit).toHaveBeenCalledWith('Firefly', 'tv', 1);
    });
});
