def _fetch(cr, query, params=None):
    cr.execute(query, params or ())
    return cr.fetchone()[0]


def _assert(condition, message):
    if not condition:
        raise RuntimeError(f"Pass 4D.3 migration aborted: {message}")


def migrate(cr, version):
    _assert(
        _fetch(cr, "SELECT to_regclass('trucalc_pass4d3_order_context') IS NULL"),
        "migration Order context table already exists",
    )
    _assert(
        _fetch(cr, "SELECT to_regclass('trucalc_pass4d3_deliverable_context') IS NULL"),
        "migration deliverable context table already exists",
    )
    _assert(
        _fetch(cr, "SELECT to_regclass('trucalc_pass4d3_event_context') IS NULL"),
        "migration event context table already exists",
    )
    cr.execute("""
        CREATE TABLE trucalc_pass4d3_order_context AS
        SELECT id, status, reviewer_user_id FROM trucalc_order ORDER BY id
    """)
    cr.execute("""
        CREATE TABLE trucalc_pass4d3_deliverable_context AS
        SELECT id, order_id, company_id, artifact_type, filename, submitted_at,
               submitted_by_id, vendor_id, engagement_id, authorization_id,
               bidding_round, version, is_current, status
          FROM trucalc_vendor_deliverable ORDER BY id
    """)
    cr.execute("""
        CREATE TABLE trucalc_pass4d3_event_context AS
        SELECT id, order_id, stable_order_id, company_id, event_type,
               from_status, to_status, actor_id, event_at, reviewer_id,
               reviewer_user_id, deliverable_id, target_valuation_id,
               new_valuation_id, revision_request_event_id,
               vendor_revision_instructions
          FROM trucalc_order_lifecycle_event ORDER BY id
    """)
