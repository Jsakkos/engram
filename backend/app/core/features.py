"""
Hardcoded feature flags.

Flip to True to expose a feature that has been merged but isn't user-ready.
Paired with frontend `src/config/constants.ts` FEATURES.
"""

# TheDiscDB lookup — disc identification + track matching. Read-only GraphQL
# queries; safe to ship.
DISCDB_LOOKUP_ENABLED = True

# TheDiscDB contributions — local export + submit/upload to thediscdb.com.
# Master switch: exposes the per-user opt-in (`discdb_contributions_enabled`,
# default False); flipping this does not submit anything automatically.
DISCDB_CONTRIBUTIONS_ENABLED = True
