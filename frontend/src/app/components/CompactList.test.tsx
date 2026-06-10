import '@testing-library/jest-dom';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { CompactList } from './CompactList';
import type { DiscData } from './DiscCard';

function makeDisc(overrides: Partial<DiscData> = {}): DiscData {
    return {
        id: '1',
        title: 'Seinfeld',
        subtitle: 'TV • SEINFELD_S4_D3',
        discLabel: 'SEINFELD_S4_D3',
        coverUrl: '/api/jobs/1/poster',
        mediaType: 'tv',
        state: 'review_needed',
        progress: 0,
        needsReview: true,
        tracks: [{ id: 1 } as never],
        tracksLoaded: true,
        ...overrides,
    };
}

function renderList(discs: DiscData[], handlers: Partial<Parameters<typeof CompactList>[0]> = {}) {
    const props = {
        discs,
        onReview: vi.fn(),
        onCancel: vi.fn(),
        onReIdentify: vi.fn(),
        ...handlers,
    };
    render(<CompactList {...props} />);
    return props;
}

describe('CompactList', () => {
    it('formats the state label instead of leaking the raw enum', () => {
        renderList([makeDisc()]);
        expect(screen.getByText('REVIEW NEEDED')).toBeInTheDocument();
        expect(screen.queryByText('review_needed')).not.toBeInTheDocument();
    });

    it('offers Review for a match-review row and wires it to the job', () => {
        const { onReview } = renderList([makeDisc()]);
        fireEvent.click(screen.getByRole('button', { name: /review/i }));
        expect(onReview).toHaveBeenCalledWith('1');
    });

    it('offers Fix title for an identity-review row instead of a dead end', () => {
        const { onReview, onReIdentify } = renderList([
            makeDisc({ identityReview: true, tracks: [] }),
        ]);
        expect(screen.queryByRole('button', { name: /^review$/i })).not.toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: /fix title/i }));
        expect(onReIdentify).toHaveBeenCalledWith('1');
        expect(onReview).not.toHaveBeenCalled();
    });

    it('holds the Fix title button until track data has loaded (mirrors DiscCard)', () => {
        renderList([makeDisc({ identityReview: true, tracks: [], tracksLoaded: false })]);
        expect(screen.queryByRole('button', { name: /fix title/i })).not.toBeInTheDocument();
    });

    it('shows Cancel only for non-terminal jobs', () => {
        renderList([
            makeDisc({ id: '1', state: 'ripping', needsReview: false }),
            makeDisc({ id: '2', state: 'completed', needsReview: false, title: 'Done Job' }),
        ]);
        expect(screen.getAllByRole('button', { name: /cancel/i })).toHaveLength(1);
    });

    it('offers "Name this disc" for a name-prompt row and wires it to onIdentify (P13)', () => {
        const onIdentify = vi.fn();
        renderList([makeDisc({ id: '1', promptKind: 'name', tracks: [] })], { onIdentify });
        fireEvent.click(screen.getByRole('button', { name: /name this disc/i }));
        expect(onIdentify).toHaveBeenCalledWith('1');
    });

    it('offers "Select season" for a season-prompt row', () => {
        const onIdentify = vi.fn();
        renderList(
            [makeDisc({ id: '1', promptKind: 'season', title: 'Eureka' })],
            { onIdentify },
        );
        fireEvent.click(screen.getByRole('button', { name: /select season/i }));
        expect(onIdentify).toHaveBeenCalledWith('1');
    });

    it('shows no identify action when the disc needs no prompt', () => {
        renderList([makeDisc({ id: '1', promptKind: null })], { onIdentify: vi.fn() });
        expect(screen.queryByRole('button', { name: /name this disc|select season/i })).not.toBeInTheDocument();
    });
});
