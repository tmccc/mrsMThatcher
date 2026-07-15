-- Documentation copy of engagement analytics schema v1.
-- The executable migration is SCHEMA_SQL in mrs_engagement_analytics.py.

-- post_pairs: one auditable main quote/context relationship.
-- post_pair_revisions: append-only discovery revisions.
-- posts: main_quote and historical_context post identities.
-- snapshot_schedule: fixed 1h, 6h, 24h, 72h and 168h targets.
-- metric_snapshots: append-only successful or terminal observations.
-- metric_snapshot_revision_audit: reason and superseded row for corrected observations.
-- collection_attempts: append-only request/transport history.
-- account_or_capability_state: collector-only cooldown and capability state.
-- schema_migrations: applied local schema versions.

-- Inspect the authoritative live schema without writing:
--   sqlite3 'file:engagement_analytics.sqlite3?mode=ro' '.schema'
