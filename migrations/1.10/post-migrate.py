def _fetch_value(cr, query, params=None):
    cr.execute(query, params or ())
    return cr.fetchone()[0]


def _assert(condition, message):
    if not condition:
        raise RuntimeError(f"Pass 4B.2C.2 post-migration validation failed: {message}")


def migrate(cr, version):
    cr.execute(
        """
        UPDATE trucalc_bid_invitation i
        SET service_type = o.service_type
        FROM trucalc_order o
        WHERE o.id = i.order_id
        """
    )
    cr.execute(
        """
        UPDATE trucalc_bid_invitation i
        SET standard_fee = fees.fee
        FROM trucalc_order o
        JOIN (
            SELECT vendor_id, service_type, min(fee) AS fee
            FROM trucalc_vendor_fee
            GROUP BY vendor_id, service_type
            HAVING count(*) = 1
        ) fees ON fees.service_type = o.service_type
        WHERE o.id = i.order_id
          AND fees.vendor_id = i.vendor_id
          AND NOT EXISTS (
              SELECT 1 FROM trucalc_pass4b2c2_ambiguous_invitation ambiguous
              WHERE ambiguous.id = i.id
          )
        """
    )

    _assert(
        _fetch_value(cr, "SELECT count(*) = 0 FROM trucalc_bid_invitation WHERE service_type IS NULL"),
        "an invitation is missing its service snapshot",
    )
    _assert(
        _fetch_value(
            cr,
            """
            SELECT count(*) = 0 FROM (
                SELECT 1 FROM trucalc_vendor_fee
                GROUP BY vendor_id, service_type HAVING count(*) > 1
            ) duplicates
            """,
        ),
        "duplicate Vendor/service fees remain",
    )
    if _fetch_value(cr, "SELECT jimmy_duplicate_cleaned FROM trucalc_pass4b2c2_context"):
        _assert(
            _fetch_value(
                cr,
                """
                SELECT count(*) = 1
                FROM trucalc_vendor_fee f
                JOIN trucalc_vendor v ON v.id = f.vendor_id
                WHERE v.name = 'Jimmy Vandergrift'
                  AND f.service_type = 'evaluation' AND f.fee = 75
                """,
            ),
            "Jimmy's authoritative $75 Evaluation fee was not preserved",
        )
    for table, column in (
        ("trucalc_vendor", "vendor_count"),
        ("trucalc_order", "order_count"),
        ("trucalc_bid", "bid_count"),
        ("trucalc_bid_invitation", "invitation_count"),
        ("trucalc_order_vendor_authorization", "authorization_count"),
    ):
        _assert(
            _fetch_value(cr, f"SELECT count(*) FROM {table}")
            == _fetch_value(cr, f"SELECT {column} FROM trucalc_pass4b2c2_context"),
            f"{table} relationship count changed",
        )
    _assert(
        _fetch_value(cr, "SELECT count(*) FROM res_users WHERE trucalc_vendor_id IS NOT NULL")
        == _fetch_value(cr, "SELECT portal_mapping_count FROM trucalc_pass4b2c2_context"),
        "Vendor Portal mapping count changed",
    )
    cr.execute("DROP TABLE trucalc_pass4b2c2_ambiguous_invitation")
    cr.execute("DROP TABLE trucalc_pass4b2c2_context")
