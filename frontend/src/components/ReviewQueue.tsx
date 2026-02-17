import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Job, DiscTitle } from '../types';
import './ReviewQueue.css';
import { useReviewState } from './ReviewQueue/hooks/useReviewState';
import { TVTitleCard } from './ReviewQueue/TVTitleCard';
import { MovieTitleCard } from './ReviewQueue/MovieTitleCard';

function ReviewQueue() {
    const { jobId } = useParams<{ jobId: string }>();
    const navigate = useNavigate();
    const [job, setJob] = useState<Job | null>(null);
    const [titles, setTitles] = useState<DiscTitle[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [isSaving, setIsSaving] = useState(false);

    const { state, handlers } = useReviewState();
    const { selectedEpisodes, selectedEditions, expandedTitles } = state;
    const { handleEpisodeChange, handleEditionChange, toggleTitleExpansion } = handlers;

    useEffect(() => {
        fetchJobDetails();
    }, [jobId]);

    const fetchJobDetails = async () => {
        try {
            // Fetch job details
            const jobResponse = await fetch(`/api/jobs/${jobId}`);
            if (jobResponse.ok) {
                const data = await jobResponse.json();
                setJob(data);
            }

            // Fetch real titles from API
            const titlesResponse = await fetch(`/api/jobs/${jobId}/titles`);
            if (titlesResponse.ok) {
                const titlesData = await titlesResponse.json();
                setTitles(titlesData);

                // Pre-fill selected episodes from match results
                titlesData.forEach((title: DiscTitle) => {
                    if (title.matched_episode) {
                        handleEpisodeChange(title.id, title.matched_episode);
                    }
                });
            }
        } catch (error) {
            console.error('Failed to fetch job:', error);
        } finally {
            setIsLoading(false);
        }
    };

    const handleSaveAll = async () => {
        setIsSaving(true);
        try {
            for (const [titleId, episodeCode] of Object.entries(selectedEpisodes)) {
                await fetch(`/api/jobs/${jobId}/review`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        title_id: parseInt(titleId),
                        episode_code: episodeCode,
                    }),
                });
            }
            navigate('/');
        } catch (error) {
            console.error('Failed to save reviews:', error);
        } finally {
            setIsSaving(false);
        }
    };

    const handleStartRip = async () => {
        try {
            await fetch(`/api/jobs/${jobId}/start`, { method: 'POST' });
            navigate('/');
        } catch (error) {
            console.error('Failed to start rip:', error);
        }
    };

    const handleSaveMovie = async (titleId: number, matchAction: 'save' | 'skip') => {
        setIsSaving(true);
        try {
            await fetch(`/api/jobs/${jobId}/review`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    title_id: titleId,
                    episode_code: matchAction === 'skip' ? 'skip' : undefined,
                    edition: matchAction === 'save' ? (selectedEditions[titleId] || null) : undefined,
                }),
            });
            navigate('/');
        } catch (error) {
            console.error('Failed to save movie review:', error);
        } finally {
            setIsSaving(false);
        }
    };

    if (isLoading) {
        return (
            <div className="review-loading">
                <span className="disc-spinner">💿</span>
                <span>Loading job details...</span>
            </div>
        );
    }

    if (!job) {
        return (
            <div className="review-error">
                <h2>Job Not Found</h2>
                <button className="btn btn-secondary" onClick={() => navigate('/')}>
                    ← Back to Dashboard
                </button>
            </div>
        );
    }

    // --- Movie Review UI ---
    if (job.content_type === 'movie') {
        return (
            <div className="review-queue movie-review">
                <div className="review-header">
                    <button className="btn btn-secondary" onClick={() => navigate('/')}>
                        ← Back
                    </button>
                    <div className="review-title">
                        <h2>Select Movie Version: {job.volume_label}</h2>
                        <p className="review-subtitle">
                            {job.detected_title}
                        </p>
                    </div>
                </div>

                <div className="review-notice">
                    ℹ️ Multiple feature-length titles found. Please select the correct version to keep.
                </div>

                <div className="titles-list">
                    <div className="titles-header">
                        <span className="col-title">Title</span>
                        <span className="col-duration">Duration</span>
                        <span className="col-size">Size</span>
                        <span className="col-res">Resolution</span>
                        <span className="col-edition">Edition Tag</span>
                        <span className="col-actions">Action</span>
                    </div>

                    {titles.map(title => (
                        <MovieTitleCard
                            key={title.id}
                            title={title}
                            selectedEdition={selectedEditions[title.id] || ''}
                            onEditionChange={handleEditionChange}
                            onSave={handleSaveMovie}
                            isSaving={isSaving}
                        />
                    ))}
                </div>
            </div>
        );
    }

    // --- TV Review UI (Existing) ---
    return (
        <div className="review-queue">
            <div className="review-header">
                <button className="btn btn-secondary" onClick={() => navigate('/')}>
                    ← Back
                </button>
                <div className="review-title">
                    <h2>Review: {job.volume_label || 'Unknown Disc'}</h2>
                    <p className="review-subtitle">
                        {job.detected_title && `${job.detected_title} `}
                        {job.detected_season && `Season ${job.detected_season}`}
                    </p>
                </div>
            </div>

            {job.error_message && (
                <div className="review-notice">
                    ⚠️ {job.error_message}
                </div>
            )}

            <div className="titles-list">
                <div className="titles-header">
                    <span className="col-title">Title</span>
                    <span className="col-duration">Duration</span>
                    <span className="col-size">Size</span>
                    <span className="col-confidence">Conf.</span>
                    <span className="col-stats">Match Stats</span>
                    <span className="col-review-reason">Review Reason</span>
                    <span className="col-episode">Episode</span>
                </div>

                {titles.map(title => (
                    <TVTitleCard
                        key={title.id}
                        title={title}
                        job={job}
                        selectedEpisode={selectedEpisodes[title.id] || ''}
                        isExpanded={expandedTitles.has(title.id)}
                        onEpisodeChange={handleEpisodeChange}
                        onToggleExpand={toggleTitleExpansion}
                    />
                ))}
            </div>

            <div className="review-actions">
                <button
                    className="btn btn-primary"
                    onClick={handleStartRip}
                    disabled={isSaving}
                >
                    ▶ Start Ripping
                </button>
                {Object.keys(selectedEpisodes).length > 0 && (
                    <button
                        className="btn btn-success"
                        onClick={handleSaveAll}
                        disabled={isSaving}
                    >
                        {isSaving ? 'Saving...' : `✓ Save ${Object.keys(selectedEpisodes).length} Assignments`}
                    </button>
                )}
            </div>
        </div>
    );
}


export default ReviewQueue;
