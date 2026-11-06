import type { DiscTitle, Job, TitleState } from '../../types';

// Mirrors start_rip_refusal in backend/app/models/disc_job.py. The backend refuses
// POST /api/jobs/{id}/start with 409 when that returns a reason, so the two must
// agree or the header offers a button that can only fail.
const PRE_RIP_STATES: ReadonlySet<TitleState> = new Set<TitleState>(['pending', 'skipped']);

/** True when the disc has not left the drive yet, so a full rip is the right
 *  action (pre-rip review: name prompt, unclear movie vs TV). A post-rip review
 *  (low-confidence match, file conflict) must not offer it: the disc is usually
 *  ejected, and partially ripped tracks have per-title re-rip instead (#710).
 *  Deselected titles count too, because movie extras are deselected after they
 *  are ripped. A completed backup also released the drive, even when every
 *  title is still pending. */
export function canStartRip(
  job: Pick<Job, 'backup_status'>,
  titles: Pick<DiscTitle, 'state'>[],
): boolean {
  if (job.backup_status === 'completed') return false;
  return titles.every((t) => PRE_RIP_STATES.has(t.state));
}

/** The user-facing message from a failed /start response: FastAPI's `detail`
 *  when the body is JSON, otherwise the raw text. */
export function startRipErrorMessage(body: string): string {
  try {
    const parsed = JSON.parse(body);
    if (typeof parsed?.detail === 'string') return parsed.detail;
  } catch {
    // Not JSON: fall through to the raw body.
  }
  return `Failed to start: ${body}`;
}
