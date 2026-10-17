import { describe, expect, it } from 'vitest';
import { titleStateBadge } from './titleState';

describe('titleStateBadge', () => {
  it('reads a never-ripped track of a failed job as NOT RIPPED, not PENDING (#713)', () => {
    expect(titleStateBadge('pending', 'failed').label).toBe('NOT RIPPED');
    expect(titleStateBadge('ripping', 'failed').label).toBe('NOT RIPPED');
  });

  it('keeps PENDING for a job that can still rip it', () => {
    expect(titleStateBadge('pending', 'review_needed').label).toBe('PENDING');
    expect(titleStateBadge('pending').label).toBe('PENDING');
  });

  it('leaves finished tracks of a failed job alone', () => {
    expect(titleStateBadge('completed', 'failed').label).toBe('OK');
    expect(titleStateBadge('failed', 'failed').label).toBe('FAIL');
  });
});
