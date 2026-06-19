import type { Job, JobState } from '../types';

export interface PromptJobs {
    namePromptJob: Job | null;
    seasonPromptJob: Job | null;
    reidentifyPromptJob: Job | null;
}

/** Which identify prompt a job should surface, if any. */
export type PromptKind = 'name' | 'season' | 'reidentify';

/** Parsed `identity_prompt_json` payload (walk-away Phase B). */
export interface IdentityPrompt {
    kind: PromptKind;
    reason: string;
}

/**
 * CTA label per prompt kind — shared by the expanded card and the compact row
 * so the two affordances can never disagree. "Confirm title" matches the
 * existing re-identify vocabulary ("Wrong title?" / "Fix title" / the
 * Re-Identify Disc modal) and stays content-type neutral.
 */
export const PROMPT_CTA_LABELS: Record<PromptKind, string> = {
    name: 'Name this disc',
    season: 'Select season',
    reidentify: 'Confirm title',
};

/**
 * Parse a job's `identity_prompt_json` (walk-away Phase B). Tolerant by
 * design: null/absent, `""` (the WS clear sentinel), malformed JSON, a
 * non-object payload, and an unknown `kind` all return null — the caller then
 * falls back to review_reason matching instead of crashing the dashboard on a
 * bad broadcast.
 */
export function parseIdentityPrompt(job: Job): IdentityPrompt | null {
    if (!job.identity_prompt_json) return null;
    let parsed: unknown;
    try {
        parsed = JSON.parse(job.identity_prompt_json);
    } catch {
        return null;
    }
    if (!parsed || typeof parsed !== 'object') return null;
    const { kind, reason } = parsed as { kind?: unknown; reason?: unknown };
    if (kind !== 'name' && kind !== 'season' && kind !== 'reidentify') return null;
    return { kind, reason: typeof reason === 'string' ? reason : '' };
}

/**
 * Classify which identify prompt a single job needs, or null. Centralized so
 * the modal opener and the on-card CTA stay in lockstep — `selectPromptJobs`
 * (which job to surface) and the card affordance (how to open it on demand)
 * both route through this one matcher. Does NOT check dismissal.
 *
 * Two sources, in priority order:
 * 1. A live `identity_prompt_json` (walk-away Phase B) — the job rips with an
 *    open identity question. Applies to RIPPING and REVIEW_NEEDED jobs (a
 *    stall-parked review job can still carry a live prompt). Other states get
 *    no CTA (e.g. a non-blocking season prompt riding into MATCHING — B6
 *    pinning or rip-end convergence retires it).
 * 2. The legacy review_reason substrings on REVIEW_NEEDED jobs — the B4
 *    rip-end conversion replays an unanswered prompt as review_reason, so
 *    this path keeps working unchanged for parked jobs.
 */
export function classifyPromptJob(job: Job): PromptKind | null {
    if (job.state === 'ripping' || job.state === 'review_needed') {
        const prompt = parseIdentityPrompt(job);
        if (prompt) return prompt.kind;
    }
    if (job.state !== 'review_needed') return null;
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
 *
 * CTA-only mid-rip: a job that is itself RIPPING carries an open identity
 * question while the rip-first walk-away rip streams in the background (Gate
 * B/C). Auto-opening a blocking modal over a *running* rip reproduces the old
 * pre-rip gate — its "Start Ripping →" / "Awaiting Input" framing reads as
 * "the rip is blocked, fill this in," inviting an accidental Cancel that kills
 * a healthy rip. So a ripping prompt job never auto-opens; it surfaces the
 * on-card CTA only. The genuine "waiting for you" moment is the rip-end
 * convergence (B4), where the unanswered prompt parks as REVIEW_NEEDED — and a
 * parked job IS eligible to auto-open below.
 *
 * SAFETY INVARIANT: this relies on `ripping → review_needed` being one-way — a
 * ripping job with an open prompt always eventually parks (or completes), so
 * the suppression is temporary and the prompt auto-opens at rip-end. If a
 * future Gate B/C rip-first branch could leave a job in `ripping` indefinitely
 * while still carrying a prompt, this guard would permanently hide the modal
 * (the on-card CTA would be the only affordance). Revisit this guard if that
 * invariant ever changes.
 */
export function shouldAutoOpenPrompt(promptJob: Job, jobs: Job[]): boolean {
    if (promptJob.state === 'ripping') return false;
    const othersActive = jobs.some(
        (j) => j.id !== promptJob.id && !TERMINAL_STATES.has(j.state),
    );
    return !othersActive;
}

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

/**
 * Pick which jobs should surface a prompt modal — review-parked jobs and
 * (walk-away Phase B) jobs that rip with an open identity question.
 *
 * Jobs whose id is in `dismissedIds` are skipped: dismissing a prompt
 * (Escape / backdrop click) leaves the job alone (a review job stays parked;
 * a ripping job keeps ripping) and must not re-open the modal on the next
 * jobs refresh. Recovery paths stay available on the job card / compact-row
 * CTA and the Review page.
 */
export function selectPromptJobs(jobs: Job[], dismissedIds: ReadonlySet<number>): PromptJobs {
    // State eligibility lives in classifyPromptJob (ripping + review for live
    // identity prompts, review-only for the review_reason fallback) — only
    // dismissal is filtered here.
    const candidates = jobs.filter((j) => !dismissedIds.has(j.id));

    const namePromptJob = candidates.find((j) => classifyPromptJob(j) === 'name') ?? null;
    const seasonPromptJob = candidates.find((j) => classifyPromptJob(j) === 'season') ?? null;
    const reidentifyPromptJob =
        candidates.find((j) => classifyPromptJob(j) === 'reidentify') ?? null;

    return { namePromptJob, seasonPromptJob, reidentifyPromptJob };
}
