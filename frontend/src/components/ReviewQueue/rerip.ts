// Mirrors RIP_FAILURE_ERROR_CODES in backend/app/services/matching_coordinator.py.
// Both must be updated together: this set gates whether the Re-rip button renders,
// so a code registered only on the backend parks tracks as re-rippable that the UI
// then offers no way to recover.
const RIP_FAILURE_CODES = new Set(['incomplete_rip', 'rip_stalled', 'rip_ejected']);

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
Object.freeze(EMPTY);

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

/** Convenience: normalize a title's raw `match_details` field (string | object |
 *  null | undefined) before parsing it. Centralizes the field-shape assumption. */
export function getRerippableStateFromTitle(
  matchDetails: string | object | null | undefined,
): RerippableState {
  const s =
    typeof matchDetails === 'string'
      ? matchDetails
      : matchDetails == null
        ? null
        : JSON.stringify(matchDetails);
  return getRerippableState(s);
}
