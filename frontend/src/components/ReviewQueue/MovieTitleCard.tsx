/**
 * Movie title card component for selecting movie versions
 */

import { DiscTitle } from '../../types';
import { formatDuration, formatSize } from './utils';
import { EditionInput } from './EditionInput';

interface MovieTitleCardProps {
    title: DiscTitle;
    selectedEdition: string;
    onEditionChange: (titleId: number, edition: string) => void;
    onSave: (titleId: number, action: 'save' | 'skip') => void;
    isSaving: boolean;
}

export function MovieTitleCard({
    title,
    selectedEdition,
    onEditionChange,
    onSave,
    isSaving,
}: MovieTitleCardProps) {
    return (
        <div className="title-row">
            <div className="col-title">
                <span className="title-index">#{title.title_index}</span>
                <div className="title-details">
                    <span className="title-name">
                        {title.output_filename ? title.output_filename.split(/[/\\]/).pop() : `Title ${title.title_index}`}
                    </span>
                    <span className="title-segments">{title.chapter_count} ch</span>
                </div>
            </div>
            <div className="col-duration">
                {formatDuration(title.duration_seconds)}
            </div>
            <div className="col-size">
                {formatSize(title.file_size_bytes)}
            </div>
            <div className="col-res">
                <span className="res-badge">{title.video_resolution || 'Unknown'}</span>
            </div>
            <EditionInput
                titleId={title.id}
                value={selectedEdition}
                onChange={onEditionChange}
            />
            <div className="col-actions">
                <button
                    className="btn btn-sm btn-primary"
                    onClick={() => onSave(title.id, 'save')}
                    disabled={isSaving}
                >
                    Select This
                </button>
                <button
                    className="btn btn-sm btn-secondary"
                    onClick={() => onSave(title.id, 'skip')}
                    disabled={isSaving}
                >
                    Discard
                </button>
            </div>
        </div>
    );
}
