/**
 * Footer status derivation (#720). Every string shown in the status bar comes
 * from data the backend actually reported (the drive list, the ASR device, the
 * jobs list); nothing here is decorative.
 */
import type { Job, JobState } from '../types';

/** States in which a job is reading the physical disc. */
const DISC_HOLDING_STATES: JobState[] = ['identifying', 'backing_up', 'ripping'];

/** Phases summarised in the band, in pipeline order. */
const PHASE_ITEMS: Array<[JobState, string]> = [
  ['identifying', 'IDENTIFYING'],
  ['backing_up', 'BACKING UP'],
  ['ripping', 'RIPPING'],
  ['matching', 'MATCHING'],
  ['organizing', 'ORGANIZING'],
];

const isPhysical = (job: Job) => job.drive_id !== 'import' && job.drive_id !== 'staging';

/**
 * Drive summary from the detected optical drives. `drives` is null until
 * `/api/drives` has answered, in which case no label is better than a guess.
 * An empty list means there is nothing to rip from (including macOS, which has
 * no drive detection), so the app is effectively import-only.
 */
export function driveFooterLabel(drives: string[] | null, jobs: Job[]): string | undefined {
  if (drives === null) return undefined;
  if (drives.length === 0) return 'IMPORT ONLY';
  const base = `${drives.length} ${drives.length === 1 ? 'DRIVE' : 'DRIVES'}`;
  const busy = jobs.some((j) => isPhysical(j) && DISC_HOLDING_STATES.includes(j.state));
  return busy ? `${base} · BUSY` : base;
}

/** Scrolling-band items: ASR device + busy flag, per-phase job counts, review queue. */
export function telemetryItems(jobs: Job[], asr: { device: string } | null): string[] {
  const items: string[] = [];
  if (asr) {
    const busy = jobs.some((j) => j.state === 'matching');
    items.push(`ASR ${asr.device.toUpperCase()} · ${busy ? 'BUSY' : 'IDLE'}`);
  }
  for (const [state, label] of PHASE_ITEMS) {
    const n = jobs.filter((j) => j.state === state).length;
    if (n > 0) items.push(`${n} ${label}`);
  }
  const waiting = jobs.filter((j) => j.state === 'review_needed').length;
  if (waiting > 0) items.push(`${waiting} AWAITING REVIEW`);
  return items;
}
