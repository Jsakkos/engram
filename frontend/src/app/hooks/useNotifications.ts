import { useEffect, useRef } from 'react';
import type { Job } from '../../types';

/**
 * Browser notifications for job state changes.
 * Requests permission on mount, fires notifications when jobs complete or fail.
 */
export function useNotifications(jobs: Job[]) {
    const prevStatesRef = useRef<Record<number, string>>({});

    // Request notification permission on mount
    useEffect(() => {
        if ('Notification' in window && Notification.permission === 'default') {
            Notification.requestPermission();
        }
    }, []);

    // Watch for state transitions to terminal states
    useEffect(() => {
        if (!('Notification' in window) || Notification.permission !== 'granted') return;

        const prevStates = prevStatesRef.current;

        for (const job of jobs) {
            const prev = prevStates[job.id];
            if (!prev || prev === job.state) continue;

            const title = job.detected_title || job.volume_label;

            if (job.state === 'completed' && prev !== 'completed') {
                new Notification('Engram - Archive Complete', {
                    body: `${title} has been archived to your library.`,
                    icon: '/vite.svg',
                });
            } else if (job.state === 'failed' && prev !== 'failed') {
                new Notification('Engram - Job Failed', {
                    body: `${title} failed: ${job.error_message || 'Unknown error'}`,
                    icon: '/vite.svg',
                });
            } else if (job.state === 'review_needed' && prev !== 'review_needed') {
                new Notification('Engram - Review Needed', {
                    body: `${title} needs your review before continuing.`,
                    icon: '/vite.svg',
                });
            }
        }

        // Update previous states
        const newStates: Record<number, string> = {};
        for (const job of jobs) {
            newStates[job.id] = job.state;
        }
        prevStatesRef.current = newStates;
    }, [jobs]);
}
