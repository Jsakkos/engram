import { useCallback, useEffect, useState } from 'react';
import type { SeasonRoster } from '../components/ReviewQueue/types';

/**
 * Loads the detected season's episode list (code + name) plus persisted
 * coverage for a job. Powers the review roster strip and labels bare episode
 * codes with real titles. Degrades gracefully: an unavailable roster (no TMDB
 * id yet, or a fetch failure) leaves `roster.available === false` and
 * `episodeName` returning ''.
 */
export function useSeasonRoster(jobId: string | undefined) {
    const [roster, setRoster] = useState<SeasonRoster | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!jobId) return;
        let cancelled = false;
        setLoading(true);
        setError(null);
        fetch(`/api/jobs/${jobId}/season-roster`)
            .then((r) => {
                // The endpoint returns 200 with available:false when there's no
                // TMDB data, so a non-OK status is a genuine failure — except a
                // 404 (job gone), which we treat as "no roster" rather than an
                // error worth surfacing.
                if (r.ok) return r.json() as Promise<SeasonRoster>;
                if (r.status === 404) return null;
                throw new Error(`season-roster ${r.status}`);
            })
            .then((data) => {
                if (!cancelled) setRoster(data);
            })
            .catch((e) => {
                if (!cancelled) {
                    setRoster(null);
                    setError(e instanceof Error ? e.message : 'season-roster failed');
                }
            })
            .finally(() => {
                if (!cancelled) setLoading(false);
            });
        return () => {
            cancelled = true;
        };
    }, [jobId]);

    const episodeName = useCallback(
        (code: string): string =>
            roster?.episodes.find((e) => e.episode_code === code)?.name ?? '',
        [roster],
    );

    return { roster, loading, error, episodeName };
}
