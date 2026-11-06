import { describe, expect, it } from 'vitest';
import { driveFooterLabel, telemetryItems } from './footerStatus';
import type { Job } from '../types';

const job = (over: Partial<Job>): Job => ({
  id: 1,
  drive_id: 'E:',
  volume_label: 'X',
  content_type: 'tv',
  state: 'ripping',
  current_speed: '',
  eta_seconds: 0,
  progress_percent: 0,
  current_title: 0,
  total_titles: 0,
  error_message: null,
  ...over,
});

describe('driveFooterLabel (#720)', () => {
  it('says nothing until the drive list is known', () => {
    expect(driveFooterLabel(null, [])).toBeUndefined();
  });

  it('reports import-only when no optical drive is detected', () => {
    expect(driveFooterLabel([], [])).toBe('IMPORT ONLY');
  });

  it('counts detected drives', () => {
    expect(driveFooterLabel(['E:'], [])).toBe('1 DRIVE');
    expect(driveFooterLabel(['E:', 'F:'], [])).toBe('2 DRIVES');
  });

  it('marks the drive busy while a job holds the physical disc', () => {
    expect(driveFooterLabel(['E:'], [job({ state: 'ripping' })])).toBe('1 DRIVE · BUSY');
  });

  it('does not call the drive busy for a post-rip or import job', () => {
    expect(driveFooterLabel(['E:'], [job({ state: 'matching' })])).toBe('1 DRIVE');
    expect(driveFooterLabel(['E:'], [job({ state: 'ripping', drive_id: 'import' })])).toBe('1 DRIVE');
  });
});

describe('telemetryItems (#720)', () => {
  it('invents nothing when nothing is known', () => {
    expect(telemetryItems([], null)).toEqual([]);
  });

  it('reports the ASR device and whether transcription is running', () => {
    expect(telemetryItems([], { device: 'cuda' })).toEqual(['ASR CUDA · IDLE']);
    expect(telemetryItems([job({ state: 'matching' })], { device: 'cpu' })).toContain('ASR CPU · BUSY');
  });

  it('summarises jobs per phase and those waiting on review', () => {
    const items = telemetryItems(
      [
        job({ id: 1, state: 'ripping' }),
        job({ id: 2, state: 'matching' }),
        job({ id: 3, state: 'matching' }),
        job({ id: 4, state: 'review_needed' }),
        job({ id: 5, state: 'completed' }),
      ],
      null,
    );
    expect(items).toEqual(['1 RIPPING', '2 MATCHING', '1 AWAITING REVIEW']);
  });
});
