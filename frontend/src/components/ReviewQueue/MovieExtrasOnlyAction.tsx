import { useState } from 'react';
import { SvActionButton, SvNotice } from '../../app/components/synapse';

interface Props {
    /** How many titles would be filed. */
    count: number;
    /** The movie whose Extras/ folder they go into. */
    movieName: string;
    disabled?: boolean;
    onConfirm: () => void;
}

/**
 * The movie review's way out for a disc with no feature on it (#813), typically
 * a bonus disc that identified as the film. Every other movie action names one
 * title the feature; this files them all under the movie's Extras/ and finishes
 * the job. Two clicks, because it moves every kept rip into the library at once.
 */
export function MovieExtrasOnlyAction({ count, movieName, disabled, onConfirm }: Props) {
    const [confirming, setConfirming] = useState(false);
    const noun = count === 1 ? 'title' : 'titles';
    return (
        <SvNotice tone="info" testid="movie-extras-only" style={{ marginBottom: 16 }}>
            <div>
                No feature on this disc? File {count === 1 ? 'the remaining title' : `all ${count} titles`}{' '}
                under {movieName}&rsquo;s Extras folder and finish the job. The movie itself is not
                touched.
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
                {confirming ? (
                    <>
                        <SvActionButton
                            tone="amber"
                            size="sm"
                            disabled={disabled}
                            onClick={() => {
                                setConfirming(false);
                                onConfirm();
                            }}
                        >
                            {`Confirm: file ${count} ${noun}`}
                        </SvActionButton>
                        <SvActionButton tone="cyan" size="sm" onClick={() => setConfirming(false)}>
                            Cancel
                        </SvActionButton>
                    </>
                ) : (
                    <SvActionButton
                        tone="cyan"
                        size="sm"
                        disabled={disabled}
                        onClick={() => setConfirming(true)}
                        title="File every remaining title as an extra of this movie"
                    >
                        File all as extras
                    </SvActionButton>
                )}
            </div>
        </SvNotice>
    );
}
