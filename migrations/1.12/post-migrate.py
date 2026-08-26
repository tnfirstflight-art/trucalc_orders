def _fetch(cr, query):
    cr.execute(query)
    return cr.fetchone()[0]


def _assert(condition, message):
    if not condition:
        raise RuntimeError(f"Pass 4C.0 post-migration validation failed: {message}")


def migrate(cr, version):
    cr.execute(
        """
        SELECT order_count, bid_count, invitation_count, authorization_count,
               tc00005_bid11
        FROM trucalc_pass4c0_context
        """
    )
    order_count, bid_count, invitation_count, authorization_count, legacy = cr.fetchone()
    _assert(_fetch(cr, "SELECT count(*) FROM trucalc_order") == order_count,
            "Order count changed")
    _assert(_fetch(cr, "SELECT count(*) FROM trucalc_bid") == bid_count,
            "Bid count changed")
    _assert(_fetch(cr, "SELECT count(*) FROM trucalc_bid_invitation") == invitation_count,
            "Invitation count changed")
    _assert(_fetch(cr, "SELECT count(*) FROM trucalc_order_vendor_authorization") == authorization_count,
            "Vendor authorization count changed")
    cr.execute(
        """
        SELECT row_to_json(current_legacy) FROM (
            SELECT o.status, o.bidding_round, o.assigned_vendor_id,
                   b.status AS bid_status, b.response_type,
                   b.proposed_delivery_date, i.is_legacy_reconstructed
            FROM trucalc_order o
            JOIN trucalc_bid b ON b.order_id = o.id
            JOIN trucalc_bid_invitation i ON i.id = b.invitation_id
            WHERE o.order_number = 'TC-00005' AND b.id = 11
        ) current_legacy
        """
    )
    current_legacy = cr.fetchone()[0]
    if legacy is not None:
        _assert(current_legacy == legacy, "approved legacy TC-00005 / Bid 11 changed")
        _assert(
            _fetch(cr, "SELECT vendor_engaged_at IS NULL FROM trucalc_order WHERE order_number='TC-00005'"),
            "a Vendor Engaged Date was fabricated for TC-00005",
        )
    cr.execute("DROP TABLE trucalc_pass4c0_context")
