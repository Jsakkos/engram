import { EPISODE_CONFIG } from "../../config/constants";
import { parseEpisodeCode } from "../ReviewQueue/coverage";
import type { SeasonRoster } from "../ReviewQueue/types";

export interface AmendEpisodeOption {
  episode: number;
  /** Episode title from the roster, or "" when the roster is unavailable. */
  name: string;
}

/**
 * Episodes the History reassign picker offers for `season`.
 *
 * The whole season, from the roster when it loaded for this season, else a
 * 1..N range. Built from sibling tracks' claims alone, the list could never
 * contain the one episode the disc was missing, which is exactly the one a
 * mis-filed track needs (#770). Every episode a sibling claims is kept too, so a
 * catalogue gap never hides a slot that is already in use.
 */
export function amendEpisodeOptions(
  roster: Pick<SeasonRoster, "available" | "season_number" | "episodes"> | null,
  titles: { matched_episode: string | null }[],
  season: number,
): AmendEpisodeOption[] {
  const names = new Map<number, string>();
  if (roster?.available && roster.season_number === season) {
    for (const ep of roster.episodes) names.set(ep.episode_number, ep.name);
  } else {
    for (let n = 1; n <= EPISODE_CONFIG.DEFAULT_EPISODES_PER_SEASON; n++) names.set(n, "");
  }
  // A combined track contributes EVERY episode it claims (S01E01-E03 -> 1, 2, 3).
  for (const t of titles) {
    const parsed = t.matched_episode ? parseEpisodeCode(t.matched_episode) : null;
    if (!parsed || parsed.season !== season) continue;
    for (const n of parsed.episodes) if (!names.has(n)) names.set(n, "");
  }
  return Array.from(names, ([episode, name]) => ({ episode, name })).sort(
    (a, b) => a.episode - b.episode,
  );
}
