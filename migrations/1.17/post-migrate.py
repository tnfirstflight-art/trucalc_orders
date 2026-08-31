def _fetch(cr, query, params=None):
    cr.execute(query, params or ())
    return cr.fetchone()[0]


def _assert(condition, message):
    if not condition:
        raise RuntimeError(f"Pass 4D.2 post-migration validation failed: {message}")


def migrate(cr, version):
    _assert(_fetch(cr, """
        SELECT count(*) = 0
          FROM trucalc_pass4d2_context snapshot
          FULL JOIN trucalc_order current USING (id)
         WHERE snapshot.id IS NULL OR current.id IS NULL
            OR snapshot.order_number IS DISTINCT FROM current.order_number
            OR snapshot.status IS DISTINCT FROM current.status
            OR snapshot.order_date IS DISTINCT FROM current.order_date
            OR snapshot.loan_amount IS DISTINCT FROM current.loan_amount
            OR snapshot.county IS DISTINCT FROM current.county
            OR snapshot.service_area_id IS DISTINCT FROM current.service_area_id
            OR snapshot.inspection_contact_name IS DISTINCT FROM current.inspection_contact_name
            OR snapshot.inspection_contact_phone IS DISTINCT FROM current.inspection_contact_phone
            OR snapshot.inspection_contact_email IS DISTINCT FROM current.inspection_contact_email
    """), "historical Order identity or protected data changed")
    _assert(_fetch(cr, """
        SELECT count(*) = 0
          FROM trucalc_pass4d2_event_context snapshot
          FULL JOIN trucalc_order_lifecycle_event current USING (id)
         WHERE snapshot.id IS NULL OR current.id IS NULL
            OR snapshot.order_id IS DISTINCT FROM current.order_id
            OR snapshot.event_type IS DISTINCT FROM current.event_type
            OR snapshot.from_status IS DISTINCT FROM current.from_status
            OR snapshot.to_status IS DISTINCT FROM current.to_status
            OR snapshot.actor_id IS DISTINCT FROM current.actor_id
            OR snapshot.event_at IS DISTINCT FROM current.event_at
    """), "historical lifecycle events changed or were fabricated")
    _assert(
        _fetch(cr, "SELECT count(*) FROM trucalc_vendor_deliverable") == 0,
        "historical Vendor deliverables were fabricated",
    )
    _assert(_fetch(cr, """
        SELECT count(*) = 0
          FROM trucalc_order_lifecycle_event
         WHERE event_type = 'valuation_received'
    """), "historical Valuation receipt events were fabricated")
    cr.execute("DROP TABLE trucalc_pass4d2_event_context")
    cr.execute("DROP TABLE trucalc_pass4d2_context")
