def _fetch(cr, query, params=None):
    cr.execute(query, params or ())
    return cr.fetchone()[0]


def _assert(condition, message):
    if not condition:
        raise RuntimeError(f"Pass 4D.2A post-migration validation failed: {message}")


def migrate(cr, version):
    _assert(_fetch(cr, """
        SELECT count(*) = 0
          FROM trucalc_pass4d2a_deliverable_context snapshot
          FULL JOIN trucalc_vendor_deliverable current USING (id)
         WHERE snapshot.id IS NULL OR current.id IS NULL
            OR snapshot.order_id IS DISTINCT FROM current.order_id
            OR snapshot.company_id IS DISTINCT FROM current.company_id
            OR snapshot.artifact_type IS DISTINCT FROM current.artifact_type
            OR snapshot.filename IS DISTINCT FROM current.filename
            OR snapshot.submitted_at IS DISTINCT FROM current.submitted_at
            OR snapshot.submitted_by_id IS DISTINCT FROM current.submitted_by_id
            OR snapshot.vendor_id IS DISTINCT FROM current.vendor_id
            OR snapshot.engagement_id IS DISTINCT FROM current.engagement_id
            OR snapshot.authorization_id IS DISTINCT FROM current.authorization_id
            OR snapshot.bidding_round IS DISTINCT FROM current.bidding_round
            OR snapshot.version IS DISTINCT FROM current.version
            OR snapshot.is_current IS DISTINCT FROM current.is_current
            OR snapshot.status IS DISTINCT FROM current.status
    """), "historical Vendor deliverables changed")
    _assert(_fetch(cr, """
        SELECT count(*) = 0
          FROM trucalc_pass4d2a_event_context snapshot
          FULL JOIN trucalc_order_lifecycle_event current USING (id)
         WHERE snapshot.id IS NULL OR current.id IS NULL
            OR snapshot.order_id IS DISTINCT FROM current.order_id
            OR snapshot.stable_order_id IS DISTINCT FROM current.stable_order_id
            OR snapshot.company_id IS DISTINCT FROM current.company_id
            OR snapshot.event_type IS DISTINCT FROM current.event_type
            OR snapshot.from_status IS DISTINCT FROM current.from_status
            OR snapshot.to_status IS DISTINCT FROM current.to_status
            OR snapshot.actor_id IS DISTINCT FROM current.actor_id
            OR snapshot.event_at IS DISTINCT FROM current.event_at
            OR snapshot.deliverable_id IS DISTINCT FROM current.deliverable_id
    """), "historical lifecycle events changed or were fabricated")
    _assert(_fetch(cr, """
        SELECT count(*) = 0
          FROM trucalc_order_lifecycle_event
         WHERE event_type IN (
             'valuation_revision_requested', 'valuation_revision_submitted'
         )
    """), "synthetic Valuation revision events were fabricated")
    cr.execute("DROP TABLE trucalc_pass4d2a_event_context")
    cr.execute("DROP TABLE trucalc_pass4d2a_deliverable_context")
