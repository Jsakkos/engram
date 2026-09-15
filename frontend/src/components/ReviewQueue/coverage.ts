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

// A code is a season plus one or more episodes: "S01E05", and the combined form
// "S01E01-E03" for one track holding several episodes (segment-format shows put
// three ~7min segments in a single 22min DVD track). Anchored so a pseudo value
// ('extra'/'skip') or a corrupted string is never read as a partial code.
const CODE_RE = /^S(\d+)((?:-?E\d+)+)$/i;
// Each episode token with the separator before it: a leading hyphen means
// "…through this episode" (the Plex/Jellyfin range form, and what hand-organized
// libraries mean by it); no hyphen means "and also this episode".
const PART_RE = /(-?)E(\d+)/gi;

/** Real "S03E05"-style codes only — excludes pseudo values like extra/skip/''. */
export function isRealCode(code: string): boolean {
    return CODE_RE.test(code);
}

/** `(season, [episodes…])` for any accepted spelling, or null for a non-code. */
export function parseEpisodeCode(code: string): { season: number; episodes: number[] } | null {
    const m = CODE_RE.exec(code ?? '');
    if (!m) return null;
    const episodes: number[] = [];
    for (const part of m[2].matchAll(PART_RE)) {
        const n = parseInt(part[2], 10);
        const prev = episodes[episodes.length - 1];
        // A backwards range ("E05-E02") is taken as a bare addition rather than
        // silently inverted — better to look odd than to invent episodes.
        const start = part[1] && prev !== undefined && n > prev ? prev + 1 : n;
        for (let i = start; i <= n; i++) if (!episodes.includes(i)) episodes.push(i);
    }
    return episodes.length ? { season: parseInt(m[1], 10), episodes } : null;
}

/** Every single-episode code a (possibly combined) code claims. */
export function episodeParts(code: string): string[] {
    const parsed = parseEpisodeCode(code);
    if (!parsed) return [];
    return parsed.episodes.map(
        (n) => `S${String(parsed.season).padStart(2, '0')}E${String(n).padStart(2, '0')}`,
    );
}

/** True when one track is assigned several episodes. */
export function isMultiEpisode(code: string): boolean {
    return (parseEpisodeCode(code)?.episodes.length ?? 0) > 1;
}

/**
 * Canonicalize an episode code: zero-padded, a contiguous run as the compact
 * `S01E01-E03` range, a gapped set as the run-on `S01E01E03` (a hyphen there
 * would claim an episode the track doesn't hold). The matcher's main path emits
 * padded codes, but a fallback path can produce unpadded ones (e.g. "S1E14");
 * without normalizing, "S1E14" and "S01E14" wouldn't dedupe/collide against each
 * other or the (always-padded) season roster. Non-codes ('extra'/'skip'/'') pass
 * through unchanged.
 */
export function normalizeEpisodeCode(code: string): string {
    const parsed = parseEpisodeCode(code);
    if (!parsed) return code;
    const { season, episodes } = parsed;
    const pad = (n: number) => String(n).padStart(2, '0');
    const head = `S${pad(season)}E${pad(episodes[0])}`;
    if (episodes.length === 1) return head;
    const contiguous = episodes.every((n, i) => n === episodes[0] + i);
    return contiguous
        ? `${head}-E${pad(episodes[episodes.length - 1])}`
        : head + episodes.slice(1).map((n) => `E${pad(n)}`).join('');
}

/**
 * How a code is shown to the user. Identical to its canonical form — which is
 * also how the organizer names the file — so the review label and the filename
 * can never disagree.
 */
export const displayEpisodeCode = normalizeEpisodeCode;

/**
 * Group the live selections by episode code → the title ids claiming it.
 * A combined track is counted against EVERY episode it claims, so the roster
 * shows all of its slots filled — and a second track claiming one of them reads
 * as the collision it is.
 */
export function assignmentsByCode(selections: Record<number, string>): Map<string, number[]> {
    const map = new Map<string, number[]>();
    for (const [titleId, code] of Object.entries(selections)) {
        if (!code || !isRealCode(code)) continue;
        for (const key of episodeParts(code)) {
            const ids = map.get(key) ?? [];
            ids.push(Number(titleId));
            map.set(key, ids);
        }
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
        const n = parseEpisodeCode(code)?.episodes[0];
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
        const code = normalizeEpisodeCode(title.matched_episode);
        out.push({
            episodeCode: code,
            episodeName: nameOf(code),
            score: title.match_confidence ?? 0,
            voteCount: details.vote_count,
            targetVotes: details.target_votes,
            isBest: true,
        });
        seen.add(code);
    }

    for (const alt of details.runner_ups ?? []) {
        if (!alt.episode) continue;
        const code = normalizeEpisodeCode(alt.episode);
        if (seen.has(code)) continue;
        seen.add(code);
        out.push({
            episodeCode: code,
            episodeName: nameOf(code),
            score: alt.confidence ?? 0,
            voteCount: alt.vote_count,
            targetVotes: alt.target_votes,
            isBest: false,
        });
    }

    return out;
}
