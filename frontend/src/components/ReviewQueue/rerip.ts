const RIP_FAILURE_CODES = new Set(['incomplete_rip', 'rip_stalled']);

export interface RerippableState {
  /** True when this REVIEW title failed at the rip level (re-rippable). */
  isRerippable: boolean;
  errorCode: string | null;
  /** User-facing message from the backend. */
  message: string | null;
  /** True while auto re-rip on reinsert is still allowed (under the cap). */
  autoEligible: boolean;
  attempts: number;
}

const EMPTY: RerippableState = {
  isRerippable: false,
  errorCode: null,
  message: null,
  autoEligible: false,
  attempts: 0,
};

/** Parse a title's `match_details` JSON into its re-rip state (Feature C). */
export function getRerippableState(matchDetails?: string | null): RerippableState {
  if (!matchDetails) return EMPTY;
  try {
    const d = JSON.parse(matchDetails);
    const code = typeof d?.error === 'string' ? d.error : null;
    if (!code || !RIP_FAILURE_CODES.has(code)) return EMPTY;
    return {
      isRerippable: true,
      errorCode: code,
      message: typeof d.message === 'string' ? d.message : null,
      autoEligible: Boolean(d.rerip_eligible),
      attempts: typeof d.rerip_attempts === 'number' ? d.rerip_attempts : 0,
    };
  } catch {
    return EMPTY;
  }
}
