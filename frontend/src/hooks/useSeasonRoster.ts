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

    useEffect(() => {
        if (!jobId) return;
        let cancelled = false;
        setLoading(true);
        fetch(`/api/jobs/${jobId}/season-roster`)
            .then((r) => (r.ok ? r.json() : null))
            .then((data: SeasonRoster | null) => {
                if (!cancelled) setRoster(data);
            })
            .catch(() => {
                if (!cancelled) setRoster(null);
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

    return { roster, loading, episodeName };
}
