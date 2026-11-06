import type { DiscTitle, Job } from '../../types';

/** Count of same-name TMDB twins recorded on the job (0 when absent/malformed). */
export function countCandidates(candidatesJson?: string | null): number {
    if (!candidatesJson) return 0;
    try {
        const parsed = JSON.parse(candidatesJson);
        return Array.isArray(parsed) ? parsed.length : 0;
    } catch {
        return 0;
    }
}

/**
 * True when the job is in review to confirm WHICH show it is, not to assign
 * episodes. Primary signal: a null `tmdb_id` (the analyst withholds the id for
 * an ambiguous/unconfirmed disc). Fallback: a same-name collision (>=2 twins)
 * the analyst kept a best-guess id for, but only while nothing has been
 * ripped/matched yet, so a genuine post-rip wrong-show disc keeps its review.
 *
 * One definition shared by the dashboard card (via the adapter) and the review
 * page, so the two can never disagree about whether a job needs identifying.
 *
 * `tmdbConfigured: false` is heuristic-only mode. A `tmdb_id` can never be
 * resolved there, so a typed show name is accepted as the identity; otherwise
 * every review job would be stuck behind an identity step it cannot complete.
 */
export function isIdentityUnresolved(
    job: Job,
    titles: DiscTitle[],
    opts: { tmdbConfigured?: boolean } = {},
): boolean {
    if (job.state !== 'review_needed') return false;
    if (opts.tmdbConfigured === false && (job.detected_title ?? '').trim() !== '') return false;
    const hasProcessedTitles = titles.some(
        // 'failed' is intentionally excluded: a fully-failed job transitions to FAILED
        // state (not review_needed), so the outer state guard makes this unreachable.
        (t) => t.state === 'matched' || t.state === 'review' || t.state === 'completed',
    );
    return (
        job.tmdb_id == null ||
        (countCandidates(job.candidates_json) >= 2 && !hasProcessedTitles)
    );
}
