/**
 * State management hook for ReviewQueue
 */

import { useState } from 'react';

export interface ReviewState {
    selectedEpisodes: Record<number, string>;
    selectedEditions: Record<number, string>;
    expandedTitles: Set<number>;
}

export interface ReviewStateHandlers {
    handleEpisodeChange: (titleId: number, episodeCode: string) => void;
    handleEditionChange: (titleId: number, edition: string) => void;
    toggleTitleExpansion: (titleId: number) => void;
}

export function useReviewState() {
    const [selectedEpisodes, setSelectedEpisodes] = useState<Record<number, string>>({});
    const [selectedEditions, setSelectedEditions] = useState<Record<number, string>>({});
    const [expandedTitles, setExpandedTitles] = useState<Set<number>>(new Set());

    const handleEpisodeChange = (titleId: number, episodeCode: string) => {
        setSelectedEpisodes(prev => ({
            ...prev,
            [titleId]: episodeCode,
        }));
    };

    const handleEditionChange = (titleId: number, edition: string) => {
        setSelectedEditions(prev => ({
            ...prev,
            [titleId]: edition,
        }));
    };

    const toggleTitleExpansion = (titleId: number) => {
        setExpandedTitles(prev => {
            const next = new Set(prev);
            if (next.has(titleId)) {
                next.delete(titleId);
            } else {
                next.add(titleId);
            }
            return next;
        });
    };

    return {
        state: {
            selectedEpisodes,
            selectedEditions,
            expandedTitles,
        },
        handlers: {
            handleEpisodeChange,
            handleEditionChange,
            toggleTitleExpansion,
        },
    };
}
