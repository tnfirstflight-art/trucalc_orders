def _fetch(cr, query, params=None):
    cr.execute(query, params or ())
    return cr.fetchone()[0]


def _assert(condition, message):
    if not condition:
        raise RuntimeError(f"Pass 4C.1 post-migration validation failed: {message}")


def migrate(cr, version):
    cr.execute(
        """
        SELECT order_count, bid_count, invitation_count, authorization_count,
               bid_audit_count, engaged_snapshot, protected_legacy_snapshot
        FROM trucalc_pass4c1_context
        """
    )
    (order_count, bid_count, invitation_count, authorization_count,
     bid_audit_count, engaged_snapshot, protected_legacy_snapshot) = cr.fetchone()
    _assert(_fetch(cr, "SELECT count(*) FROM trucalc_order") == order_count,
            "Order count changed")
    _assert(_fetch(cr, "SELECT count(*) FROM trucalc_bid") == bid_count,
            "Bid count changed")
    _assert(_fetch(cr, "SELECT count(*) FROM trucalc_bid_invitation") == invitation_count,
            "Invitation count changed")
    _assert(
        _fetch(cr, "SELECT count(*) FROM trucalc_order_vendor_authorization")
        == authorization_count,
        "Vendor authorization count changed",
    )
    _assert(_fetch(cr, "SELECT count(*) FROM trucalc_bid_audit") == bid_audit_count,
            "Bid audit count changed")

    cr.execute(
        """
        SELECT o.id, o.company_id, o.bidding_round, o.assigned_vendor_id,
               o.vendor_fee, o.vendor_delivery_date, o.vendor_engaged_at,
               b.id AS bid_id, a.id AS authorization_id, ba.id AS audit_id,
               ba.actor_id, ba.event_at
        FROM trucalc_order o
        JOIN trucalc_order_vendor_authorization a
          ON a.order_id = o.id
         AND a.vendor_id = o.assigned_vendor_id
         AND a.company_id = o.company_id
         AND a.round_number = o.bidding_round
         AND a.source = 'assignment' AND a.active IS TRUE
        JOIN trucalc_bid b
          ON b.order_id = o.id
         AND b.vendor_id = o.assigned_vendor_id
         AND b.round_number = o.bidding_round
         AND b.status = 'selected'
         AND b.response_type IS NOT NULL
        JOIN trucalc_bid_audit ba
          ON ba.order_id = o.id AND ba.bid_id = b.id
         AND ba.action = 'vendor_engaged'
        WHERE o.status = 'engaged'
          AND o.vendor_delivery_date IS NOT NULL
          AND o.vendor_engaged_at IS NOT NULL
          AND ba.event_at = o.vendor_engaged_at
          AND (ba.new_values->>'assigned_vendor_id')::integer = o.assigned_vendor_id
          AND (ba.new_values->>'vendor_delivery_date')::date = o.vendor_delivery_date
          AND (ba.new_values->>'vendor_engaged_at')::timestamp = o.vendor_engaged_at
        ORDER BY o.id
        """
    )
    modern = cr.fetchall()
    engaged_count = _fetch(cr, "SELECT count(*) FROM trucalc_order WHERE status='engaged'")
    _assert(len(modern) == engaged_count,
            "an Engaged Order lacks unique modern engagement provenance")
    _assert(len({row[0] for row in modern}) == len(modern),
            "an Engaged Order has duplicate modern engagement provenance")

    for row in modern:
        (order_id, company_id, round_number, vendor_id, fee, delivery_date,
         engaged_at, bid_id, authorization_id, audit_id, actor_id, event_at) = row
        cr.execute(
            """
            INSERT INTO trucalc_vendor_engagement
                (order_id, vendor_id, company_id, round_number, source_bid_id,
                 assignment_authorization_id, source_engagement_audit_id,
                 response_state, agreed_vendor_fee, vendor_delivery_date,
                 engaged_at, engaged_by_id, active,
                 create_uid, write_uid, create_date, write_date)
            VALUES (%s, %s, %s, %s, %s, %s, %s,
                    'awaiting_acceptance', %s, %s, %s, %s, TRUE,
                    %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (order_id, vendor_id, company_id, round_number, bid_id,
             authorization_id, audit_id, fee, delivery_date, engaged_at,
             actor_id, actor_id, actor_id, event_at, event_at),
        )

    _assert(
        _fetch(cr, "SELECT count(*) FROM trucalc_vendor_engagement") == engaged_count,
        "active engagement aggregate count does not match Engaged Orders",
    )
    _assert(
        _fetch(cr, "SELECT count(*) FROM trucalc_vendor_engagement_event") == 0,
        "migration fabricated a Vendor engagement-response event",
    )
    cr.execute(
        """
        SELECT jsonb_agg(row_to_json(current_engaged) ORDER BY current_engaged.id)
        FROM (
            SELECT id, order_number, status, company_id, bidding_round,
                   assigned_vendor_id, vendor_fee, vendor_delivery_date,
                   vendor_engaged_at
            FROM trucalc_order WHERE status = 'engaged' ORDER BY id
        ) current_engaged
        """
    )
    _assert(cr.fetchone()[0] == engaged_snapshot, "Engaged Order facts changed")
    cr.execute(
        """
        SELECT jsonb_agg(row_to_json(legacy) ORDER BY legacy.id)
        FROM (
            SELECT id, order_number, status, bidding_round, assigned_vendor_id,
                   vendor_fee, vendor_delivery_date, vendor_engaged_at
            FROM trucalc_order
            WHERE status = 'assigned' OR order_number = 'TC-00005'
            ORDER BY id
        ) legacy
        """
    )
    _assert(cr.fetchone()[0] == protected_legacy_snapshot,
            "protected legacy Order facts changed")
    _assert(
        _fetch(
            cr,
            """SELECT count(*) FROM trucalc_vendor_engagement e
                 JOIN trucalc_order o ON o.id=e.order_id
                WHERE o.status <> 'engaged'""",
        ) == 0,
        "a legacy non-Engaged Order received an engagement aggregate",
    )
    cr.execute("DROP TABLE trucalc_pass4c1_context")
