import { describe, expect, it } from 'vitest';
import type { TitleState } from '../../types';
import { canStartRip } from './startRip';

const t = (state: TitleState, is_selected = true) => ({ state, is_selected });

describe('canStartRip (#710)', () => {
  it('allows a pre-rip review where every selected title is pending', () => {
    expect(canStartRip([t('pending'), t('pending')])).toBe(true);
  });

  it('allows a job with no titles yet (name prompt before scan)', () => {
    expect(canStartRip([])).toBe(true);
  });

  it('treats skipped titles as not ripped', () => {
    expect(canStartRip([t('pending'), t('skipped', false)])).toBe(true);
  });

  it.each<TitleState>(['review', 'matched', 'completed', 'failed', 'queued', 'ripping'])(
    'hides Start rip once a selected title is %s',
    (state) => {
      expect(canStartRip([t('pending'), t(state)])).toBe(false);
    },
  );

  it('ignores the state of deselected titles', () => {
    expect(canStartRip([t('pending'), t('failed', false)])).toBe(true);
  });
});
