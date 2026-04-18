"""
Hardcoded feature flags.

Flip to True to expose a feature that has been merged but isn't user-ready.
Paired with frontend `src/config/constants.ts` FEATURES.
"""

# TheDiscDB integration — lookups, contributions, match-source UI.
# Keep False until the API contract and UX are validated with real users.
DISCDB_ENABLED = False
