import { useState, useEffect } from 'react';

/**
 * Returns a formatted elapsed time string that updates every second.
 * Shows "Xm Ys" for times under an hour, "Xh Ym" for longer.
 */
export function useElapsedTime(startedAt?: string): string | null {
    const [elapsed, setElapsed] = useState<string | null>(null);

    useEffect(() => {
        if (!startedAt) {
            setElapsed(null);
            return;
        }

        const startTime = new Date(startedAt).getTime();

        function update() {
            const diff = Math.max(0, Math.floor((Date.now() - startTime) / 1000));
            const hours = Math.floor(diff / 3600);
            const minutes = Math.floor((diff % 3600) / 60);
            const seconds = diff % 60;

            if (hours > 0) {
                setElapsed(`${hours}h ${minutes}m`);
            } else if (minutes > 0) {
                setElapsed(`${minutes}m ${seconds}s`);
            } else {
                setElapsed(`${seconds}s`);
            }
        }

        update();
        const interval = setInterval(update, 1000);
        return () => clearInterval(interval);
    }, [startedAt]);

    return elapsed;
}
