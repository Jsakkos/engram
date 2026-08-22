import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { EpisodePicker } from './EpisodePicker';
import type { RosterEpisode } from './types';

const SEASON: RosterEpisode[] = [
    { episode_code: 'S01E01', episode_number: 1, name: 'Changes', runtime: 7 },
    { episode_code: 'S01E02', episode_number: 2, name: 'The Big Sister', runtime: 7 },
    { episode_code: 'S01E03', episode_number: 3, name: 'Maternal Combat', runtime: 7 },
    { episode_code: 'S01E04', episode_number: 4, name: 'Dodgeball', runtime: 7 },
    { episode_code: 'S01E05', episode_number: 5, name: 'Sports Day', runtime: 7 },
];

function renderPicker(props: Partial<Parameters<typeof EpisodePicker>[0]> = {}) {
    const onAssign = vi.fn();
    render(
        <EpisodePicker
            titleIndex={1}
            season={1}
            episodes={SEASON}
            selection={undefined}
            trackSeconds={null}
            onAssign={onAssign}
            {...props}
        />,
    );
    return { onAssign, input: screen.getByLabelText('Manual episode for title 1') };
}

describe('EpisodePicker search', () => {
    it('filters by episode name as you type', () => {
        const { input } = renderPicker();
        fireEvent.change(input, { target: { value: 'dodgeball' } });
        expect(screen.getByText('Dodgeball')).toBeInTheDocument();
        expect(screen.queryByText('Changes')).not.toBeInTheDocument();
    });

    it('treats a bare number as an episode number', () => {
        const { input } = renderPicker();
        fireEvent.change(input, { target: { value: '3' } });
        const options = screen.getAllByRole('option');
        expect(options).toHaveLength(1);
        expect(options[0]).toHaveTextContent('Maternal Combat');
    });

    it('assigns the clicked episode', () => {
        const { input, onAssign } = renderPicker();
        fireEvent.focus(input);
        fireEvent.click(screen.getByText('The Big Sister'));
        expect(onAssign).toHaveBeenCalledWith('S01E02');
    });

    it('assigns the first match on Enter', () => {
        const { input, onAssign } = renderPicker();
        fireEvent.change(input, { target: { value: 'maternal' } });
        fireEvent.keyDown(input, { key: 'Enter' });
        expect(onAssign).toHaveBeenCalledWith('S01E03');
    });

    it('says so when nothing matches', () => {
        const { input } = renderPicker();
        fireEvent.change(input, { target: { value: 'zzz' } });
        expect(screen.getByText(/No episode matches/)).toBeInTheDocument();
        expect(screen.queryAllByRole('option')).toHaveLength(0);
    });

    it('falls back to a numbered list when the roster is unavailable', () => {
        const { input, onAssign } = renderPicker({ episodes: [] });
        fireEvent.change(input, { target: { value: '2' } });
        fireEvent.keyDown(input, { key: 'Enter' });
        expect(onAssign).toHaveBeenCalledWith('S01E02');
    });
});

describe('EpisodePicker combined tracks', () => {
    it('assigns a range when the track holds several episodes', () => {
        // 22min against 7min segments → the picker offers a 3-episode span.
        const { input, onAssign } = renderPicker({ trackSeconds: 22.3 * 60 });
        fireEvent.focus(input);
        fireEvent.click(screen.getByText('Changes'));
        expect(onAssign).toHaveBeenCalledWith('S01E01-E03');
    });

    it('rests at one episode for an ordinary track, without suggesting a span', () => {
        // The stepper is always present — one that appeared only when the runtime
        // heuristic fired was missing from exactly the track that needed setting
        // by hand — but it makes no claim about an ordinary episode.
        renderPicker({ trackSeconds: 7 * 60 });
        expect(screen.getByText('1 episode')).toBeInTheDocument();
        expect(screen.queryByText(/runtime suggests/)).not.toBeInTheDocument();
    });

    it('suggests a span for a padded combined track', () => {
        renderPicker({ trackSeconds: 24.65 * 60 });
        expect(screen.getByText('3 episodes')).toBeInTheDocument();
        expect(screen.getByText(/runtime suggests 3/)).toBeInTheDocument();
    });

    it('re-emits the range when the span changes', () => {
        const { onAssign } = renderPicker({ selection: 'S01E01-E02', trackSeconds: 14 * 60 });
        fireEvent.click(screen.getByLabelText('More episodes'));
        expect(onAssign).toHaveBeenCalledWith('S01E01-E03');
    });

    it('shrinks a range back to a single episode', () => {
        const { onAssign } = renderPicker({ selection: 'S01E01-E02', trackSeconds: 14 * 60 });
        fireEvent.click(screen.getByLabelText('Fewer episodes'));
        expect(onAssign).toHaveBeenCalledWith('S01E01');
    });

    it('never runs a range past the end of the season', () => {
        const { input, onAssign } = renderPicker({ trackSeconds: 22.3 * 60 });
        fireEvent.focus(input);
        fireEvent.click(screen.getByText('Sports Day')); // E05, the last one
        expect(onAssign).toHaveBeenCalledWith('S01E05');
    });
});
