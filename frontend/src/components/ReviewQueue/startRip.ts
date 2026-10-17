import type { DiscTitle, TitleState } from '../../types';

// Mirrors rip_has_started in backend/app/models/disc_job.py. The backend refuses
// POST /api/jobs/{id}/start with 409 once this is true, so the two must agree or
// the header offers a button that can only fail.
const PRE_RIP_STATES: ReadonlySet<TitleState> = new Set<TitleState>(['pending', 'skipped']);

/** True when nothing selected has been ripped yet, so a full rip is the right
 *  action (pre-rip review: name prompt, unclear movie vs TV). A post-rip review
 *  (low-confidence match, file conflict) must not offer it: the disc is usually
 *  ejected, and partially ripped tracks have per-title re-rip instead (#710). */
export function canStartRip(titles: Pick<DiscTitle, 'is_selected' | 'state'>[]): boolean {
  return !titles.some((t) => t.is_selected && !PRE_RIP_STATES.has(t.state));
}
