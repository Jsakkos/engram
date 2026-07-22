import '@testing-library/jest-dom';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import IdentityFields from './IdentityFields';

describe('IdentityFields', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        results: [
          { tmdb_id: 4589, name: 'Arrested Development', type: 'tv', year: '2003', poster_path: null, popularity: 20 },
        ],
      }),
    }) as never;
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('reports title changes to the parent', () => {
    const onChange = vi.fn();
    render(
      <IdentityFields
        value={{ title: '', contentType: 'tv', season: '1', tmdbId: undefined }}
        onChange={onChange}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText(/e\.g\./i), { target: { value: 'The Office' } });

    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ title: 'The Office' }));
  });

  it('clears tmdbId when the title is typed manually', () => {
    const onChange = vi.fn();
    render(
      <IdentityFields
        value={{ title: 'Arrested Development', contentType: 'tv', season: '1', tmdbId: 4589 }}
        onChange={onChange}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText(/e\.g\./i), { target: { value: 'Arrested Dev' } });

    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ tmdbId: undefined }));
  });

  it('hides the season field for movies', () => {
    render(
      <IdentityFields
        value={{ title: 'Inception', contentType: 'movie', season: '1', tmdbId: undefined }}
        onChange={vi.fn()}
      />,
    );

    expect(screen.queryByText(/season/i)).not.toBeInTheDocument();
  });

  it('selecting a search result sets title, type and tmdbId together', async () => {
    const onChange = vi.fn();
    render(
      <IdentityFields
        value={{ title: '', contentType: 'movie', season: '1', tmdbId: undefined }}
        onChange={onChange}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText(/search/i), { target: { value: 'arrested' } });
    await vi.advanceTimersByTimeAsync(600);
    fireEvent.click(await screen.findByText('Arrested Development'));

    await waitFor(() =>
      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ title: 'Arrested Development', contentType: 'tv', tmdbId: 4589 }),
      ),
    );
  });
});
