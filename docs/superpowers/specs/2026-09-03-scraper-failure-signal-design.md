# Scraper Failure Signal Design

**Date:** 2026-09-03
**Status:** Approved, implementing
**Closes:** the "Known gap, deliberately out of scope: the scrapers have no failure signal"
section of `docs/superpowers/plans/2026-08-31-subtitle-cache-harvester-repair.md`.

## Problem

PR #631 taught the subtitle-cache harvester to decline to record a coverage row when a
season could not actually be measured, on the principle that **no record beats a false
zero**: absence means measure later, `0.0` means never retry for 30 days. But
`_is_degraded` in `app/matcher/testing_service.py` covers the OpenSubtitles half of the
cascade only, and says so in its own docstring.

`app/matcher/provider_scheduler.py` emits exactly two per-episode statuses, `downloaded`
and `not_found`. Its `_CircuitBreaker` trips after three consecutive transport failures
and thereafter fast-skips the provider without changing the result shape. So when
OpenSubtitles is healthy but BOTH Addic7ed and TVsubtitles are down:

- every episode returns `not_found`
- `os_failed` is False
- the season records at 0%
- `should_skip` suppresses it for 30 days

That is the identical defect PR #631 repaired, reached through a different door.

## Consumer audit

`run_jobs` has exactly two production consumers, both in `testing_service.py`:

| Site | Function | Role |
|---|---|---|
| `testing_service.py:381` | `_fetch_episodes` | precomputed-gap heal |
| `testing_service.py:875` | `download_subtitles` | main season harvest |

Plus 12 call sites in `tests/unit/test_provider_scheduler.py` and 7
`patch.object(ts, "run_jobs", return_value={})` sites in
`tests/unit/test_testing_service_degraded.py`.

## Design

### 1. Signal source: the breaker trip, not raw exception counts

`_CircuitBreaker` already computes the needed distinction and throws it away.
`record_failure` counts **only exceptions**; a clean miss or an unusable download calls
`record_success` because the provider *responded*. That is exactly "this provider had
nothing" versus "this provider never answered".

A provider is **failed for this season** when its breaker tripped, or a job was
fast-skipped because its breaker was open. Deliberately NOT "had at least one transport
failure": a single transient scraper exception is routine, and flagging on it would mark
nearly every season degraded, write no coverage rows at all, and walk straight into the
other known gap ("a persistently degraded corpus is re-measured forever"). Three
consecutive transport failures is an already-tuned "this provider is down" verdict.

### 2. Combination rule

Widen `_is_degraded`'s first gate; keep its second clause unchanged.

```python
def _is_degraded(os_failed, episodes, *, providers_failed=False):
    if not (os_failed or providers_failed):
        return False
    if not episodes:
        return True
    return any(ep.get("status") not in RETRIEVED_STATUSES for ep in episodes)
```

This answers the partial-result question by inheritance rather than by new reasoning. If
Addic7ed is dead but TVsubtitles served every episode, the season is complete: there is
no gap an outage could be hiding, so it records normally. If the season is incomplete, we
cannot distinguish "genuinely absent" from "was behind the dead provider", so we decline
to measure.

The PR #631 asymmetry holds here for the same reason it held there: over-flagging costs
one cheap retry on the next run, under-flagging costs a 30-day blind spot.

### 3. Breaker-open for a season never attempted

Not reachable today. `run_jobs` constructs a fresh `Scheduler` per call and
`download_subtitles` calls it once per season, so every breaker starts closed at season
boundaries. A trip therefore always means "this provider went down *during this season*".
A test pins this so it stays true.

Making breakers process-global is a **genuinely independent** follow-up (it is a
performance change: stop re-paying three timeouts per season for a provider already known
down). It is not entangled with this change, but landing it would make this question
live: a breaker tripped during season 3 would suppress attempts in season 4, and a
never-attempted provider would then have to count as failed for that season under exactly
the same asymmetry argument. Recorded in a code comment at the decision point.

### 4. Return contract

`run_jobs` returns a `SchedulerRun` dataclass:

```python
@dataclass(frozen=True)
class ProviderHealth:
    name: str
    responded: bool          # at least one non-exception response
    transport_failures: int  # exceptions raised talking to the provider
    breaker_tripped: bool    # breaker opened at some point this run
    skipped_by_breaker: int  # jobs fast-skipped while the breaker was open

@dataclass(frozen=True)
class SchedulerRun:
    results: dict[str, dict]
    provider_health: dict[str, ProviderHealth]

    @property
    def failed_providers(self) -> list[str]: ...
```

Rejected alternative: a `dict` subclass carrying the health as an attribute. Zero test
churn, but consumers would need `getattr(results, "failed_providers", ())`, so a mocked
plain dict silently reads "no failures" -- reintroducing precisely the silent-underflag
mode this change exists to remove. Breaking the stale mocks loudly is the point.

`Scheduler.run` keeps returning the plain results dict (it is the internal collector);
`Scheduler.provider_health()` is a new accessor and `run_jobs` composes the two.

The health record is richer than the bare `set[str]` the predicate needs because the
harvester-repair plan explicitly wanted a detection half: a WARNING naming the dead
provider and its failure count is what lets an operator act before a misconfiguration
runs for weeks.

### 5. Heal path in scope

`_fetch_episodes` (the precomputed-gap heal) is the same door: if its scrapers are down
and gaps remain, `_heal_precomputed_gaps` still reports `degraded: False` because the
value is copied from the precomputed skip dict. `_fetch_episodes` returns its provider
health alongside the results, and `_heal_precomputed_gaps` sets `degraded=True` when
providers failed AND episodes are still missing -- the same combination rule.

## Testing

Assert observable effects, not mock call counts, per what worked in PR #631:

- `_is_degraded` truth table over the new `providers_failed` axis, including the
  one-dead-one-healthy-but-complete case that must NOT degrade.
- `run_jobs` against an always-raising fake client: `failed_providers` names it,
  `breaker_tripped` is True, and the per-episode results are still `not_found` (shape
  unchanged for existing consumers).
- `run_jobs` against a client that only ever misses: `failed_providers` is empty
  (responded, had nothing).
- `download_subtitles` end to end with healthy OpenSubtitles and dead scrapers, asserting
  `result["degraded"] is True` -- the exact scenario that is silently mis-recorded today.
- Build-script level: `_should_record_coverage` declines, so no coverage row is written.

Each new test is verified to fail against pre-change code.

## Out of scope

- Process-global breaker state (independent follow-up, see 3).
- A degraded-run circuit breaker for the "persistently degraded corpus" runaway; still a
  design decision, unchanged by this work.
