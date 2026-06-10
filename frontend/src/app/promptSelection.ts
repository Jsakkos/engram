import type { Job } from '../types';

export interface PromptJobs {
    namePromptJob: Job | null;
    seasonPromptJob: Job | null;
}

/**
 * Pick which review jobs should surface a blocking prompt modal.
 *
 * Jobs whose id is in `dismissedIds` are skipped: dismissing a prompt
 * (Escape / backdrop click) parks the job in review instead of cancelling
 * it, and must not re-open the modal on the next jobs refresh. Recovery
 * paths stay available on the job card ("Wrong title?") and the Review page.
 */
export function selectPromptJobs(jobs: Job[], dismissedIds: ReadonlySet<number>): PromptJobs {
    const inReview = jobs.filter((j) => j.state === 'review_needed' && !dismissedIds.has(j.id));

    const namePromptJob =
        inReview.find(
            (j) =>
                (j.review_reason?.includes('label unreadable') && !j.detected_title) ||
                (j.review_reason?.includes('merged without separators') && j.content_type === 'tv'),
        ) ?? null;

    const seasonPromptJob =
        inReview.find((j) => j.review_reason?.includes('select a season')) ?? null;

    return { namePromptJob, seasonPromptJob };
}
