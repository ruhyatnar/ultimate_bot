/**
 * Freshness budgets for engine-published health, in ONE place.
 *
 * The engine stamps its WS snapshot with `updated_ms` and its decision-loop
 * heartbeat with `cycle_ms`; status.py turns both into an `age_s`. Several
 * surfaces then had to decide "is that age still trustworthy?" — and each one
 * hardcoded its own `30`, so tuning the budget meant finding every copy and a
 * missed one silently contradicted the others (the stream lights could call the
 * engine healthy while the health card called it stale, for the same payload).
 *
 * These live in the client, not the payload: `age_s` is a fact the engine
 * publishes, the tolerance is a presentation decision.
 */

/** Age (s) after which an engine-published snapshot stops being trusted. */
export const ENGINE_FRESHNESS_S = 30;

/** True when an engine snapshot age has blown its freshness budget. */
export const engineIsStale = (ageS: number | null | undefined): boolean =>
  typeof ageS === 'number' && ageS > ENGINE_FRESHNESS_S;

/** True when the age is known and still inside the budget (null = unknown). */
export const engineIsFresh = (ageS: number | null | undefined): boolean =>
  typeof ageS === 'number' && ageS <= ENGINE_FRESHNESS_S;
