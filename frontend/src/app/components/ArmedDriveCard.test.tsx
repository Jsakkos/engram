import '@testing-library/jest-dom';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import ArmedDriveCard from './ArmedDriveCard';

describe('ArmedDriveCard', () => {
  const identity = { title: 'Arrested Development', content_type: 'tv', season: 1, tmdb_id: 4589, disc_number: null };

  it('shows the locked identity and target drive', () => {
    render(<ArmedDriveCard driveId="E:" identity={identity} onDisarm={vi.fn()} />);

    expect(screen.getByText(/Arrested Development/)).toBeInTheDocument();
    expect(screen.getByText(/E:/)).toBeInTheDocument();
    expect(screen.getByText(/season 1/i)).toBeInTheDocument();
  });

  it('calls onDisarm when dismissed', () => {
    const onDisarm = vi.fn();
    render(<ArmedDriveCard driveId="E:" identity={identity} onDisarm={onDisarm} />);

    fireEvent.click(screen.getByRole('button', { name: /disarm/i }));

    expect(onDisarm).toHaveBeenCalledWith('E:');
  });

  it('omits the season line for movies', () => {
    render(
      <ArmedDriveCard
        driveId="E:"
        identity={{ ...identity, content_type: 'movie', season: null }}
        onDisarm={vi.fn()}
      />,
    );

    expect(screen.queryByText(/season/i)).not.toBeInTheDocument();
  });
});
