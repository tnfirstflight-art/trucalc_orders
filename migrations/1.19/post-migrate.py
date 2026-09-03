def _fetch(cr, query, params=None):
    cr.execute(query, params or ())
    return cr.fetchone()[0]


def _assert(condition, message):
    if not condition:
        raise RuntimeError(f"Pass 4D.3 post-migration validation failed: {message}")


def migrate(cr, version):
    _assert(_fetch(cr, """
        SELECT count(*) = 0
          FROM trucalc_pass4d3_order_context snapshot
          FULL JOIN trucalc_order current USING (id)
         WHERE snapshot.id IS NULL OR current.id IS NULL
            OR snapshot.status IS DISTINCT FROM current.status
            OR snapshot.reviewer_user_id IS DISTINCT FROM current.reviewer_user_id
    """), "historical Order state or Reviewer assignments changed")
    _assert(_fetch(cr, """
        SELECT count(*) = 0
          FROM trucalc_pass4d3_deliverable_context snapshot
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
          FROM trucalc_pass4d3_event_context snapshot
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
            OR snapshot.reviewer_id IS DISTINCT FROM current.reviewer_id
            OR snapshot.reviewer_user_id IS DISTINCT FROM current.reviewer_user_id
            OR snapshot.deliverable_id IS DISTINCT FROM current.deliverable_id
            OR snapshot.target_valuation_id IS DISTINCT FROM current.target_valuation_id
            OR snapshot.new_valuation_id IS DISTINCT FROM current.new_valuation_id
            OR snapshot.revision_request_event_id IS DISTINCT FROM current.revision_request_event_id
            OR snapshot.vendor_revision_instructions IS DISTINCT FROM current.vendor_revision_instructions
    """), "historical lifecycle events changed")
    _assert(_fetch(cr, """
        SELECT count(*) = 0 FROM trucalc_order_lifecycle_event
         WHERE event_type IN ('review_accepted', 'valuation_approved', 'reviewer_reassigned')
    """), "synthetic 4D.3 lifecycle events were fabricated")
    cr.execute("DROP TABLE trucalc_pass4d3_event_context")
    cr.execute("DROP TABLE trucalc_pass4d3_deliverable_context")
    cr.execute("DROP TABLE trucalc_pass4d3_order_context")
