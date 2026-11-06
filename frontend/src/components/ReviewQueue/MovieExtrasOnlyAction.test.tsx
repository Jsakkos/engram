import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { MovieExtrasOnlyAction } from './MovieExtrasOnlyAction';

describe('MovieExtrasOnlyAction (#813)', () => {
    it('says how many titles it would file, and where', () => {
        render(<MovieExtrasOnlyAction count={3} movieName="The Dark Knight" onConfirm={() => {}} />);
        const text = screen.getByTestId('movie-extras-only').textContent ?? '';
        expect(text).toContain('3 titles');
        expect(text).toContain('The Dark Knight');
        expect(text).toContain('Extras');
    });

    it('reads naturally for a single title', () => {
        render(<MovieExtrasOnlyAction count={1} movieName="The Dark Knight" onConfirm={() => {}} />);
        expect(screen.getByTestId('movie-extras-only').textContent).toContain('File the remaining title under');
    });

    it('asks for a confirmation before filing', async () => {
        const onConfirm = vi.fn();
        render(<MovieExtrasOnlyAction count={2} movieName="The Dark Knight" onConfirm={onConfirm} />);

        await userEvent.click(screen.getByRole('button', { name: 'File all as extras' }));
        expect(onConfirm).not.toHaveBeenCalled();

        await userEvent.click(screen.getByRole('button', { name: 'Confirm: file 2 titles' }));
        expect(onConfirm).toHaveBeenCalledTimes(1);
    });

    it('can back out of the confirmation', async () => {
        const onConfirm = vi.fn();
        render(<MovieExtrasOnlyAction count={1} movieName="The Dark Knight" onConfirm={onConfirm} />);

        await userEvent.click(screen.getByRole('button', { name: 'File all as extras' }));
        await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));

        expect(screen.getByRole('button', { name: 'File all as extras' })).toBeTruthy();
        expect(onConfirm).not.toHaveBeenCalled();
    });

    it('does not file while a save is in flight', async () => {
        const onConfirm = vi.fn();
        render(<MovieExtrasOnlyAction count={2} movieName="X" disabled onConfirm={onConfirm} />);

        await userEvent.click(screen.getByRole('button', { name: 'File all as extras' }));

        expect(screen.queryByRole('button', { name: /Confirm/ })).toBeNull();
        expect(onConfirm).not.toHaveBeenCalled();
    });
});
