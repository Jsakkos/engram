import '@testing-library/jest-dom';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import ArmDiscModal from './ArmDiscModal';

describe('ArmDiscModal', () => {
  beforeEach(() => {
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: 'armed' }) }) as never;
  });
  afterEach(() => vi.restoreAllMocks());

  it('disables arming until a title is entered', () => {
    render(<ArmDiscModal driveId="E:" onClose={vi.fn()} onArmed={vi.fn()} />);
    expect(screen.getByTestId('arm-submit')).toBeDisabled();
  });

  it('posts the armed identity and reports success', async () => {
    const onArmed = vi.fn();
    render(<ArmDiscModal driveId="E:" onClose={vi.fn()} onArmed={onArmed} />);

    fireEvent.change(screen.getByPlaceholderText(/e\.g\./i), { target: { value: 'The Office' } });
    fireEvent.click(screen.getByTestId('arm-submit'));

    await waitFor(() => expect(onArmed).toHaveBeenCalled());
    const [url, init] = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe('/api/manual/arm');
    expect(JSON.parse(init.body)).toMatchObject({
      drive_id: 'E:',
      title: 'The Office',
      content_type: 'tv',
      season: 1,
    });
  });

  it('omits season for movies', async () => {
    render(<ArmDiscModal driveId="E:" onClose={vi.fn()} onArmed={vi.fn()} />);

    fireEvent.change(screen.getByPlaceholderText(/e\.g\./i), { target: { value: 'Inception' } });
    fireEvent.click(screen.getByRole('button', { name: /movie/i }));
    fireEvent.click(screen.getByTestId('arm-submit'));

    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    const [, init] = (global.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(JSON.parse(init.body).season).toBeNull();
  });

  it('surfaces a 409 as a readable conflict message', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ detail: 'Drive E: already has an active job.' }),
    }) as never;
    render(<ArmDiscModal driveId="E:" onClose={vi.fn()} onArmed={vi.fn()} />);

    fireEvent.change(screen.getByPlaceholderText(/e\.g\./i), { target: { value: 'The Office' } });
    fireEvent.click(screen.getByTestId('arm-submit'));

    expect(await screen.findByRole('alert')).toHaveTextContent(/already has an active job/i);
  });
});
