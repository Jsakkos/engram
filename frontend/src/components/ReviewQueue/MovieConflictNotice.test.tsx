import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { MovieConflictNotice } from './MovieConflictNotice';

const MESSAGE = 'File already exists: /movies/Hairspray (2007)/Hairspray (2007) {edition-Theatrical}.mkv';

describe('MovieConflictNotice (#685)', () => {
    it('names the file that is already in the library', () => {
        render(<MovieConflictNotice message={MESSAGE} onResolve={() => {}} />);
        expect(screen.getByTestId('movie-conflict-notice').textContent).toContain(MESSAGE);
    });

    it.each([
        ['Replace existing', 'overwrite'],
        ['Keep both', 'rename'],
    ])('"%s" resolves with %s', async (label, resolution) => {
        const onResolve = vi.fn();
        render(<MovieConflictNotice message={MESSAGE} onResolve={onResolve} />);

        await userEvent.click(screen.getByRole('button', { name: label }));

        expect(onResolve).toHaveBeenCalledWith(resolution);
    });

    it('does not resolve while a save is in flight', async () => {
        const onResolve = vi.fn();
        render(<MovieConflictNotice message={MESSAGE} disabled onResolve={onResolve} />);

        await userEvent.click(screen.getByRole('button', { name: 'Replace existing' }));

        expect(onResolve).not.toHaveBeenCalled();
    });
});
