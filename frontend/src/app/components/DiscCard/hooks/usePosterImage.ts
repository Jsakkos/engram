/**
 * Hook for fetching poster images with retry logic
 */

import { useState, useEffect } from 'react';
import { UI_CONFIG } from '../../../../config/constants';

export function usePosterImage(discId: string, discTitle: string) {
    const [posterUrl, setPosterUrl] = useState<string | null>(null);

    useEffect(() => {
        let retryCount = 0;

        const fetchPoster = async () => {
            try {
                const response = await fetch(`/api/jobs/${discId}/poster`);
                if (response.ok) {
                    const data = await response.json();
                    if (data.poster_url) {
                        setPosterUrl(data.poster_url);
                    } else if (retryCount < UI_CONFIG.POSTER_MAX_RETRIES) {
                        // Retry after configured delay if no poster yet (job might be initializing)
                        retryCount++;
                        setTimeout(fetchPoster, UI_CONFIG.POSTER_FETCH_RETRY_DELAY_MS);
                    }
                }
            } catch (error) {
                console.error("Failed to fetch poster:", error);
            }
        };

        fetchPoster();
    }, [discId, discTitle]); // Re-fetch when title changes

    return posterUrl;
}
