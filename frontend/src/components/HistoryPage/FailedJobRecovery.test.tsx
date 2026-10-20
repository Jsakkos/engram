import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { FailedJobRecovery } from './FailedJobRecovery';

function stubFetch(recovery: Record<string, unknown>) {
  const fetchMock = vi.fn((url: string) =>
    String(url).endsWith('/recovery')
      ? Promise.resolve({ ok: true, json: () => Promise.resolve(recovery) })
      : Promise.resolve({ ok: true, json: () => Promise.resolve({ job_id: 99, retried_from: 7 }) }),
  );
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

const base = {
  job_id: 7, staging_path: 'D:/staging/SHOW_S1D1', staging_exists: true,
  file_count: 2, can_retry: true, reason: null,
};

describe('FailedJobRecovery', () => {
  it('retries from staging after an inline confirm', async () => {
    const fetchMock = stubFetch(base);
    render(<FailedJobRecovery jobId={7} />);

    fireEvent.click(await screen.findByRole('button', { name: /Retry from staging/ }));
    expect(fetchMock).not.toHaveBeenCalledWith('/api/jobs/7/retry-from-staging', expect.anything());
    fireEvent.click(screen.getByRole('button', { name: 'Start retry' }));

    expect(await screen.findByText('Retried as job #99.')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith('/api/jobs/7/retry-from-staging', { method: 'POST' });
  });

  it('explains why there is no retry when staging is gone, and still offers the path', async () => {
    stubFetch({ ...base, staging_exists: false, file_count: 0, can_retry: false,
      reason: 'The staging folder no longer exists (it may have been cleaned up).' });
    render(<FailedJobRecovery jobId={7} />);

    expect(await screen.findByText(/no longer exists/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Retry from staging/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Open folder/ })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Copy path/ })).toBeInTheDocument();
  });

  it('opens the staging folder through the backend', async () => {
    const fetchMock = stubFetch(base);
    render(<FailedJobRecovery jobId={7} />);
    fireEvent.click(await screen.findByRole('button', { name: /Open folder/ }));
    expect(fetchMock).toHaveBeenCalledWith('/api/jobs/7/open-staging', { method: 'POST' });
  });
});
