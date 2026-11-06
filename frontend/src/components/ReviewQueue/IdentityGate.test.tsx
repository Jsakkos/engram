import '@testing-library/jest-dom';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { Job } from '../../types';
import { IdentityGate } from './IdentityGate';

const twins = JSON.stringify([
    { tmdb_id: 1, name: 'Frasier', year: '1993', popularity: 9 },
    { tmdb_id: 2, name: 'Frasier', year: '2023', popularity: 4 },
]);

function makeJob(overrides: Partial<Job> = {}): Job {
    return {
        id: 7,
        state: 'review_needed',
        content_type: 'tv',
        detected_title: 'Frasier',
        detected_season: 1,
        tmdb_id: null,
        ...overrides,
    } as Job;
}

describe('IdentityGate', () => {
    it('leads with identifying the disc and says why episodes are locked', () => {
        render(<IdentityGate job={makeJob()} trackCount={8} onIdentify={vi.fn()} />);
        expect(screen.getByRole('heading', { name: /identify this disc/i })).toBeInTheDocument();
        expect(screen.getByText(/8 tracks/i)).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /identify disc/i })).toBeInTheDocument();
    });

    it('opens the existing re-identify dialog', () => {
        render(<IdentityGate job={makeJob({ candidates_json: twins })} trackCount={2} onIdentify={vi.fn()} />);
        fireEvent.click(screen.getByRole('button', { name: /identify disc/i }));
        expect(screen.getByRole('dialog')).toBeInTheDocument();
    });

    it('hands a picked candidate to onIdentify and closes the dialog', async () => {
        const onIdentify = vi.fn();
        render(<IdentityGate job={makeJob({ candidates_json: twins })} trackCount={2} onIdentify={onIdentify} />);
        fireEvent.click(screen.getByRole('button', { name: /identify disc/i }));
        fireEvent.click(screen.getByText(/Frasier \(2023\)/));
        expect(onIdentify).toHaveBeenCalledWith('Frasier', 'tv', 1, 2);
        await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    });

    it('shows the failure when identifying does not go through', () => {
        render(<IdentityGate job={makeJob()} trackCount={2} error="boom" onIdentify={vi.fn()} />);
        expect(screen.getByText(/boom/)).toBeInTheDocument();
    });
});
