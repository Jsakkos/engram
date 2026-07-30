import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { TitleList } from './TitleList';
import type { DiscTitle } from '../../types';

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

function renderList(titles: DiscTitle[]) {
    return render(
        <TitleList
            titles={titles}
            selectedTitleId={titles[0]?.id ?? null}
            selections={{}}
            collisions={new Set()}
            episodeName={() => ''}
            onSelect={vi.fn()}
            selectedIds={new Set()}
            onToggleSelect={vi.fn()}
        />,
    );
}

describe('TitleList: organize failure (#563)', () => {
    it('flags a track whose file could not be moved', () => {
        renderList([
            makeTitle({
                match_details: JSON.stringify({
                    error: 'organize_failed',
                    message: "[Errno 13] Permission denied: '/library/tv'",
                }),
            }),
        ]);
        expect(screen.getByText('save failed')).toBeInTheDocument();
    });

    it('keeps the existing file-exists flag distinct', () => {
        renderList([
            makeTitle({
                match_details: JSON.stringify({
                    error: 'file_exists',
                    message: 'File already exists: /library/tv/x.mkv',
                }),
            }),
        ]);
        expect(screen.getByText('file exists')).toBeInTheDocument();
        expect(screen.queryByText('save failed')).not.toBeInTheDocument();
    });

    it('flags nothing for an ordinary unmatched track', () => {
        renderList([makeTitle()]);
        expect(screen.queryByText('save failed')).not.toBeInTheDocument();
        expect(screen.queryByText('file exists')).not.toBeInTheDocument();
    });
});
