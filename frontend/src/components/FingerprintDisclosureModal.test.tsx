import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { FingerprintDisclosureModal } from './FingerprintDisclosureModal';

describe('FingerprintDisclosureModal', () => {
    it('renders the disclosure title and pending count', () => {
        render(
            <FingerprintDisclosureModal
                pendingCount={3}
                pseudonym="abcd-1234"
                serverUrl="https://fp.example.com/v1"
                onAccept={vi.fn().mockResolvedValue(undefined)}
                onDecline={vi.fn().mockResolvedValue(undefined)}
            />,
        );

        expect(
            screen.getByText(/Engram is about to start contributing audio fingerprints/i),
        ).toBeInTheDocument();
        expect(screen.getByText('3')).toBeInTheDocument();
        expect(screen.getByText('abcd-1234')).toBeInTheDocument();
        expect(screen.getByText('https://fp.example.com/v1')).toBeInTheDocument();
    });

    it('calls onAccept when accept button clicked', async () => {
        const user = userEvent.setup();
        const onAccept = vi.fn().mockResolvedValue(undefined);
        const onDecline = vi.fn().mockResolvedValue(undefined);

        render(
            <FingerprintDisclosureModal
                pendingCount={3}
                pseudonym="abcd-1234"
                serverUrl="https://fp.example.com/v1"
                onAccept={onAccept}
                onDecline={onDecline}
            />,
        );

        await user.click(screen.getByRole('button', { name: /accept and start contributing/i }));
        expect(onAccept).toHaveBeenCalledTimes(1);
    });

    it('calls onDecline when decline button clicked', async () => {
        const user = userEvent.setup();
        const onAccept = vi.fn().mockResolvedValue(undefined);
        const onDecline = vi.fn().mockResolvedValue(undefined);

        render(
            <FingerprintDisclosureModal
                pendingCount={3}
                pseudonym="abcd-1234"
                serverUrl="https://fp.example.com/v1"
                onAccept={onAccept}
                onDecline={onDecline}
            />,
        );

        await user.click(screen.getByRole('button', { name: /disable contributions/i }));
        expect(onDecline).toHaveBeenCalledTimes(1);
    });
});
