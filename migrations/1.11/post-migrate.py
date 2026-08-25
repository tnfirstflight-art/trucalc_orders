def _fetch(cr, query):
    cr.execute(query)
    return cr.fetchone()[0]


def _assert(condition, message):
    if not condition:
        raise RuntimeError(f"Pass 4B.2C.3 post-migration validation failed: {message}")


def migrate(cr, version):
    cr.execute(
        """
        SELECT order_count, invitation_count, authorization_count, bid_count, fee_count
        FROM trucalc_pass4b2c3_context
        """
    )
    expected = cr.fetchone()
    actual = (
        _fetch(cr, "SELECT count(*) FROM trucalc_order"),
        _fetch(cr, "SELECT count(*) FROM trucalc_bid_invitation"),
        _fetch(cr, "SELECT count(*) FROM trucalc_order_vendor_authorization"),
        _fetch(cr, "SELECT count(*) FROM trucalc_bid"),
        _fetch(cr, "SELECT count(*) FROM trucalc_vendor_fee"),
    )
    _assert(actual == expected, "protected business record counts changed")
    _assert(
        _fetch(cr, "SELECT count(*) FROM trucalc_bid_invitation WHERE requested_delivery_date IS NOT NULL") == 0,
        "a historical invitation requested delivery date was fabricated",
    )
    _assert(
        _fetch(cr, "SELECT count(*) FROM trucalc_bid WHERE response_type IS NOT NULL") == 0,
        "a legacy Bid was converted to a canonical response",
    )
    _assert(
        _fetch(cr, "SELECT count(*) FROM trucalc_order WHERE vendor_delivery_date IS NOT NULL") == 0,
        "a historical assignment Vendor Delivery Date was fabricated",
    )
    cr.execute("SELECT array_agg(id ORDER BY id) FROM trucalc_bid WHERE invitation_id = 8")
    _assert(cr.fetchone()[0] == [2, 3], "legacy Invitation 8 Bids were not preserved")
    _assert(
        _fetch(
            cr,
            """SELECT count(*) FROM pg_indexes
               WHERE tablename = 'trucalc_bid'
                 AND indexdef ILIKE '%UNIQUE%invitation_id%response_type IS NOT NULL%'""",
        ) >= 1,
        "canonical response partial uniqueness is missing",
    )
    cr.execute("DROP TABLE trucalc_pass4b2c3_context")
