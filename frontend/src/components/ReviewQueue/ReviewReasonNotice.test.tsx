import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ReviewReasonNotice } from './ReviewReasonNotice';

const REASON =
    '4 track(s) were already filed to /tv/Show/Season 01 and were not re-matched. ' +
    'If that is the wrong season or show, move them by hand. Confirm the remaining tracks below.';

describe('ReviewReasonNotice', () => {
    it("shows the job's review reason", () => {
        render(<ReviewReasonNotice reason={REASON} />);
        expect(screen.getByTestId('review-reason-notice').textContent).toContain(REASON);
    });

    it.each([null, undefined, ''])('renders nothing for %s', (reason) => {
        const { container } = render(<ReviewReasonNotice reason={reason} />);
        expect(container.innerHTML).toBe('');
    });
});
