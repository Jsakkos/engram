import type { Job, JobState } from '../types';

export interface PromptJobs {
    namePromptJob: Job | null;
    seasonPromptJob: Job | null;
}

/** Which identify prompt a review job should surface, if any. */
export type PromptKind = 'name' | 'season';

/**
 * Classify which identify prompt a single job needs from its review reason, or
 * null. Centralized so the modal opener and the on-card CTA stay in lockstep —
 * `selectPromptJobs` (which job to surface) and the card affordance (how to
 * open it on demand) both route through this one matcher. Does NOT check job
 * state or dismissal; callers filter on `review_needed` as needed.
 */
export function classifyPromptJob(job: Job): PromptKind | null {
    if (
        (job.review_reason?.includes('label unreadable') && !job.detected_title) ||
        (job.review_reason?.includes('merged without separators') && job.content_type === 'tv')
    ) {
        return 'name';
    }
    if (job.review_reason?.includes('select a season')) {
        return 'season';
    }
    return null;
}

/** Job states that are NOT terminal — work the user might still be watching. */
const TERMINAL_STATES: ReadonlySet<JobState> = new Set<JobState>(['completed', 'failed']);

/**
 * Whether a review prompt should auto-open its blocking modal over the
 * dashboard, or only be surfaced non-modally (the on-card CTA).
 *
 * P13: auto-opening the instant a review job appears steals focus from
 * whatever the user was doing (e.g. watching another disc rip — and combined
 * with the dismissal fix, an absent-minded Escape no longer destroys the job,
 * but the interruption alone is the problem). We auto-open only when
 * `promptJob` is the *only* active job — nothing else to interrupt — which
 * preserves the zero-friction single-disc path: insert one disc, walk away,
 * and the prompt is waiting. Stale completed/failed cards don't count as
 * active, so they never suppress that waiting prompt. When other jobs are
 * busy, the prompt waits behind the card CTA instead.
 */
export function shouldAutoOpenPrompt(promptJob: Job, jobs: Job[]): boolean {
    const othersActive = jobs.some(
        (j) => j.id !== promptJob.id && !TERMINAL_STATES.has(j.state),
    );
    return !othersActive;
}

/**
 * Pick which review jobs should surface a blocking prompt modal.
 *
 * Jobs whose id is in `dismissedIds` are skipped: dismissing a prompt
 * (Escape / backdrop click) parks the job in review instead of cancelling
 * it, and must not re-open the modal on the next jobs refresh. Recovery
 * paths stay available on the job card ("Wrong title?") and the Review page.
 */
/**
 * Drop dismissed ids whose jobs no longer exist. SQLite's auto-increment
 * resets after a DEBUG reset-all-jobs, so a fresh job can reuse a
 * previously-dismissed id — without pruning, its prompt would be silently
 * suppressed. Mutates the set in place (it lives in a ref).
 */
export function pruneDismissedIds(dismissedIds: Set<number>, jobs: Job[]): void {
    const liveIds = new Set(jobs.map((j) => j.id));
    for (const id of dismissedIds) {
        if (!liveIds.has(id)) dismissedIds.delete(id);
    }
}

export function selectPromptJobs(jobs: Job[], dismissedIds: ReadonlySet<number>): PromptJobs {
    const inReview = jobs.filter((j) => j.state === 'review_needed' && !dismissedIds.has(j.id));

    const namePromptJob = inReview.find((j) => classifyPromptJob(j) === 'name') ?? null;
    const seasonPromptJob = inReview.find((j) => classifyPromptJob(j) === 'season') ?? null;

    return { namePromptJob, seasonPromptJob };
}
