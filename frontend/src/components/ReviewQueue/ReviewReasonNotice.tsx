import { SvNotice } from '../../app/components/synapse';

/** The job-level reason it was parked in review, as written by the backend. */
export function ReviewReasonNotice({ reason }: { reason?: string | null }) {
    if (!reason) return null;
    return (
        <SvNotice tone="warn" testid="review-reason-notice">
            › {reason}
        </SvNotice>
    );
}
