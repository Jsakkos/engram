import { describe, expect, it } from 'vitest';
import type { TitleState } from '../../types';
import { canStartRip, startRipErrorMessage } from './startRip';

const t = (state: TitleState) => ({ state });
const noBackup = { backup_status: null };

describe('canStartRip (#710)', () => {
  it('allows a pre-rip review where every title is pending', () => {
    expect(canStartRip(noBackup, [t('pending'), t('pending')])).toBe(true);
  });

  it('allows a job with no titles yet (name prompt before scan)', () => {
    expect(canStartRip(noBackup, [])).toBe(true);
  });

  it('treats skipped titles as not ripped', () => {
    expect(canStartRip(noBackup, [t('pending'), t('skipped')])).toBe(true);
  });

  it.each<TitleState>(['review', 'matched', 'completed', 'failed', 'queued', 'ripping'])(
    'hides Start rip once a title is %s',
    (state) => {
      expect(canStartRip(noBackup, [t('pending'), t(state)])).toBe(false);
    },
  );

  it('hides Start rip after a completed backup released the drive', () => {
    expect(canStartRip({ backup_status: 'completed' }, [t('pending')])).toBe(false);
  });

  it('keeps Start rip when the backup failed and nothing was ripped', () => {
    expect(canStartRip({ backup_status: 'failed' }, [t('pending')])).toBe(true);
  });
});

describe('startRipErrorMessage', () => {
  it('shows the 409 detail rather than raw JSON', () => {
    expect(startRipErrorMessage('{"detail":"This disc has already been ripped."}')).toBe(
      'This disc has already been ripped.',
    );
  });

  it('falls back to the raw body when it is not JSON', () => {
    expect(startRipErrorMessage('Internal Server Error')).toBe(
      'Failed to start: Internal Server Error',
    );
  });
});
