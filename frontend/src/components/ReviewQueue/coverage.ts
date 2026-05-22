/**
 * Pure coverage/conflict logic for the disc review screen.
 *
 * The matcher already ranks candidates per title; this module turns the user's
 * *current, unsaved* episode selections into disc-level coverage — which
 * episodes are assigned, doubled (collision), or missing (a gap inside the
 * disc's covered range) — and surfaces the obvious gap to suggest for an
 * unmatched title. Kept free of React so it can be unit-tested directly.
 */

import type { DiscTitle } from '../../types';
import { parseMatchDetails } from './utils';

export type { RosterEpisode } from './types';
import type { RosterEpisode } from './types';

export type EpisodeStatus = 'assigned' | 'duplicate' | 'missing' | 'off';

export interface CoverageEntry {
    status: EpisodeStatus;
    titleIds: number[];
}

export interface Candidate {
    episodeCode: string;
    episodeName: string;
    score: number;
    voteCount?: number;
    targetVotes?: number;
    isBest: boolean;
}

const CODE_RE = /^S(\d+)E(\d+)$/i;

/** Real "S03E05"-style codes only — excludes pseudo values like extra/skip/''. */
function isRealCode(code: string): boolean {
    return CODE_RE.test(code);
}

function episodeNumber(code: string): number | null {
    const m = CODE_RE.exec(code);
    return m ? parseInt(m[2], 10) : null;
}

/** Group the live selections by episode code → the title ids claiming it. */
export function assignmentsByCode(selections: Record<number, string>): Map<string, number[]> {
    const map = new Map<string, number[]>();
    for (const [titleId, code] of Object.entries(selections)) {
        if (!code || !isRealCode(code)) continue;
        const ids = map.get(code) ?? [];
        ids.push(Number(titleId));
        map.set(code, ids);
    }
    return map;
}

/**
 * Per-episode coverage keyed by episode code. "missing" applies only inside the
 * covered range [min..max] of assigned episodes — episodes outside that range
 * are "off" (i.e. on another disc), matching the server's snapshot logic.
 */
export function computeCoverage(
    selections: Record<number, string>,
    episodes: RosterEpisode[],
): Record<string, CoverageEntry> {
    const byCode = assignmentsByCode(selections);

    const presentNums: number[] = [];
    for (const code of byCode.keys()) {
        const n = episodeNumber(code);
        if (n != null) presentNums.push(n);
    }
    const lo = presentNums.length ? Math.min(...presentNums) : 0;
    const hi = presentNums.length ? Math.max(...presentNums) : -1;

    const out: Record<string, CoverageEntry> = {};
    for (const ep of episodes) {
        const ids = byCode.get(ep.episode_code) ?? [];
        let status: EpisodeStatus;
        if (ids.length > 1) status = 'duplicate';
        else if (ids.length === 1) status = 'assigned';
        else if (ep.episode_number >= lo && ep.episode_number <= hi) status = 'missing';
        else status = 'off';
        out[ep.episode_code] = { status, titleIds: ids };
    }
    return out;
}

/** The lowest unfilled episode inside the covered range — the gap to suggest. */
export function suggestGapCode(
    selections: Record<number, string>,
    episodes: RosterEpisode[],
): string | null {
    const cov = computeCoverage(selections, episodes);
    const gaps = episodes
        .filter((ep) => cov[ep.episode_code]?.status === 'missing')
        .sort((a, b) => a.episode_number - b.episode_number);
    return gaps.length ? gaps[0].episode_code : null;
}

/** Episode codes claimed by more than one title — the collisions to resolve. */
export function collidingCodes(selections: Record<number, string>): Set<string> {
    const set = new Set<string>();
    for (const [code, ids] of assignmentsByCode(selections)) {
        if (ids.length > 1) set.add(code);
    }
    return set;
}

/** Ranked candidates for a title: best match first, then runner-ups, named. */
export function buildCandidates(
    title: DiscTitle,
    nameOf: (code: string) => string,
): Candidate[] {
    const details = parseMatchDetails(title);
    const out: Candidate[] = [];
    const seen = new Set<string>();

    if (title.matched_episode) {
        out.push({
            episodeCode: title.matched_episode,
            episodeName: nameOf(title.matched_episode),
            score: title.match_confidence ?? 0,
            voteCount: details.vote_count,
            targetVotes: details.target_votes,
            isBest: true,
        });
        seen.add(title.matched_episode);
    }

    for (const alt of details.runner_ups ?? []) {
        if (!alt.episode || seen.has(alt.episode)) continue;
        seen.add(alt.episode);
        out.push({
            episodeCode: alt.episode,
            episodeName: nameOf(alt.episode),
            score: alt.confidence ?? 0,
            voteCount: alt.vote_count,
            targetVotes: alt.target_votes,
            isBest: false,
        });
    }

    return out;
}
