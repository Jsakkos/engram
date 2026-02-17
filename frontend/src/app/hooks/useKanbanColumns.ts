/**
 * Kanban column organization and filtering logic
 */

import { useState, useMemo } from 'react';
import type { Job, DiscTitle } from '../../types';
import { transformJobToDiscData } from '../../types/adapters';
import { generateMockDiscs } from '../utils/mockData';

export function useKanbanColumns(
    jobs: Job[],
    titlesMap: Record<number, DiscTitle[]>,
    devMode: boolean = false
) {
    const [filter, setFilter] = useState<"all" | "active" | "completed">("active");

    // Transform jobs to disc data for display
    const discsData = useMemo(() => {
        if (devMode) {
            return generateMockDiscs();
        }
        return jobs.map(job => ({
            ...transformJobToDiscData(job, titlesMap[job.id] || []),
            needsReview: job.state === 'review_needed',
        }));
    }, [jobs, titlesMap, devMode]);

    // Filter discs based on current filter
    const filteredDiscs = useMemo(() => {
        return discsData.filter((disc) => {
            if (filter === "active") {
                return disc.state !== "completed" && disc.state !== "error";
            }
            if (filter === "completed") {
                return disc.state === "completed";
            }
            return true;
        });
    }, [discsData, filter]);

    // Calculate counts for filter badges
    const activeCount = useMemo(() => {
        return discsData.filter((d) => d.state !== "completed" && d.state !== "error").length;
    }, [discsData]);

    const completedCount = useMemo(() => {
        return discsData.filter((d) => d.state === "completed").length;
    }, [discsData]);

    // Organize discs into Kanban columns
    const columns = useMemo(() => {
        return {
            scanning: filteredDiscs.filter((d) => d.state === "scanning" || d.state === "idle"),
            ripping: filteredDiscs.filter((d) => d.state === "ripping" || d.state === "archiving_iso"),
            processing: filteredDiscs.filter((d) => d.state === "ripping"), // Processing happens during ripping
            review: filteredDiscs.filter((d) => d.needsReview === true),
            done: filteredDiscs.filter((d) => d.state === "completed" || d.state === "error"),
        };
    }, [filteredDiscs]);

    return {
        filter,
        setFilter,
        discsData,
        filteredDiscs,
        columns,
        activeCount,
        completedCount,
    };
}
