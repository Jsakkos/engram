import '@testing-library/jest-dom';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { SubtitleUploadModal } from './SubtitleUploadModal';
import * as client from '../../api/client';

vi.mock('../../api/client', async () => {
  const actual = await vi.importActual<typeof client>('../../api/client');
  return { ...actual, previewManualSubtitles: vi.fn(), commitManualSubtitles: vi.fn() };
});

function makeFile(name: string, content: string): File {
  return new File([content], name, { type: 'text/plain' });
}

describe('SubtitleUploadModal', () => {
  beforeEach(() => {
    vi.mocked(client.previewManualSubtitles).mockReset();
    vi.mocked(client.commitManualSubtitles).mockReset();
  });

  it('opens the modal and previews selected files', async () => {
    vi.mocked(client.previewManualSubtitles).mockResolvedValue([
      { filename: 'Show.S01E05.srt', season: 1, episode: 5, status: 'ready' },
    ]);
    const onImported = vi.fn();
    render(<SubtitleUploadModal jobId={7} onImported={onImported} />);

    fireEvent.click(screen.getByRole('button', { name: /upload subtitles/i }));
    const input = screen.getByTestId('subtitle-upload-input');
    fireEvent.change(input, {
      target: { files: [makeFile('Show.S01E05.srt', '1\n00:00:01,000 --> 00:00:02,000\nHi\n')] },
    });

    await waitFor(() =>
      expect(client.previewManualSubtitles).toHaveBeenCalledWith(7, [
        { filename: 'Show.S01E05.srt', content: '1\n00:00:01,000 --> 00:00:02,000\nHi\n' },
      ]),
    );
    expect(await screen.findByText('S01E05')).toBeInTheDocument();
  });

  it('commits confirmed files and calls onImported', async () => {
    vi.mocked(client.previewManualSubtitles).mockResolvedValue([
      { filename: 'Show.S01E05.srt', season: 1, episode: 5, status: 'ready' },
    ]);
    vi.mocked(client.commitManualSubtitles).mockResolvedValue([
      { filename: 'Show.S01E05.srt', season: 1, episode: 5, status: 'imported' },
    ]);
    const onImported = vi.fn();
    render(<SubtitleUploadModal jobId={7} onImported={onImported} />);

    fireEvent.click(screen.getByRole('button', { name: /upload subtitles/i }));
    const input = screen.getByTestId('subtitle-upload-input');
    fireEvent.change(input, {
      target: { files: [makeFile('Show.S01E05.srt', '1\n00:00:01,000 --> 00:00:02,000\nHi\n')] },
    });
    await screen.findByText('S01E05');

    fireEvent.click(screen.getByRole('button', { name: /import/i }));

    await waitFor(() =>
      expect(client.commitManualSubtitles).toHaveBeenCalledWith(7, [
        { filename: 'Show.S01E05.srt', season: 1, episode: 5, content: '1\n00:00:01,000 --> 00:00:02,000\nHi\n' },
      ]),
    );
    await waitFor(() => expect(onImported).toHaveBeenCalled());
  });

  it('does not preselect an already-covered file for import', async () => {
    vi.mocked(client.previewManualSubtitles).mockResolvedValue([
      { filename: 'Show.S01E02.srt', season: 1, episode: 2, status: 'already_covered' },
    ]);
    render(<SubtitleUploadModal jobId={7} onImported={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: /upload subtitles/i }));
    const input = screen.getByTestId('subtitle-upload-input');
    fireEvent.change(input, { target: { files: [makeFile('Show.S01E02.srt', 'content')] } });

    const checkbox = await screen.findByRole('checkbox', { name: /S01E02/i });
    expect(checkbox).not.toBeChecked();
  });
});
