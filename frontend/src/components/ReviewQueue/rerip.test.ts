import { describe, expect, it } from 'vitest';
import { getRerippableState } from './rerip';

describe('getRerippableState', () => {
  it('detects an auto-eligible incomplete_rip title', () => {
    const md = JSON.stringify({ error: 'incomplete_rip', message: 'clean it', rerip_eligible: true, rerip_attempts: 0 });
    const s = getRerippableState(md);
    expect(s.isRerippable).toBe(true);
    expect(s.autoEligible).toBe(true);
    expect(s.errorCode).toBe('incomplete_rip');
    expect(s.message).toBe('clean it');
  });

  it('detects a cap-reached rip_stalled title as rerippable but not auto', () => {
    const md = JSON.stringify({ error: 'rip_stalled', rerip_eligible: false, rerip_attempts: 2 });
    const s = getRerippableState(md);
    expect(s.isRerippable).toBe(true);
    expect(s.autoEligible).toBe(false);
    expect(s.attempts).toBe(2);
  });

  it('returns not-rerippable for a match-level review code (low_confidence)', () => {
    expect(getRerippableState(JSON.stringify({ error: 'low_confidence' })).isRerippable).toBe(false);
  });

  it('returns not-rerippable for null input', () => {
    expect(getRerippableState(null).isRerippable).toBe(false);
  });

  it('returns not-rerippable for unparseable input', () => {
    expect(getRerippableState('not json').isRerippable).toBe(false);
  });

  it('attempts defaults to 0 when rerip_attempts is absent from an incomplete_rip payload', () => {
    const md = JSON.stringify({ error: 'incomplete_rip', rerip_eligible: true });
    const s = getRerippableState(md);
    expect(s.isRerippable).toBe(true);
    expect(s.attempts).toBe(0);
  });

  it('rip_stalled with rerip_eligible true yields autoEligible true', () => {
    const md = JSON.stringify({ error: 'rip_stalled', rerip_eligible: true, rerip_attempts: 1 });
    const s = getRerippableState(md);
    expect(s.isRerippable).toBe(true);
    expect(s.autoEligible).toBe(true);
    expect(s.attempts).toBe(1);
  });
});
