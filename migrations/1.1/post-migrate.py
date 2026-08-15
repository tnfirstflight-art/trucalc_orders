def _fetch_value(cr, query, params=None):
    cr.execute(query, params or ())
    return cr.fetchone()[0]


def _assert(condition, message):
    if not condition:
        raise RuntimeError(f"Pass 4A.2 post-migration validation failed: {message}")


def _validate_immutable_bids(cr):
    _assert(
        _fetch_value(
            cr,
            """
            SELECT count(*) = 0
            FROM (
                SELECT
                    COALESCE(b.id, s.id) AS id
                FROM trucalc_bid b
                FULL JOIN trucalc_pass4a2_bid_snapshot s ON s.id = b.id
                WHERE b.id IS NULL
                   OR s.id IS NULL
                   OR b.order_id IS DISTINCT FROM s.order_id
                   OR b.vendor_id IS DISTINCT FROM s.vendor_id
                   OR b.company_id IS DISTINCT FROM s.company_id
                   OR b.bid_amount IS DISTINCT FROM s.bid_amount
                   OR b.turn_time_days IS DISTINCT FROM s.turn_time_days
                   OR b.notes IS DISTINCT FROM s.notes
                   OR b.create_uid IS DISTINCT FROM s.create_uid
                   OR b.create_date IS DISTINCT FROM s.create_date
            ) mismatches
            """,
        ),
        "immutable legacy bid values changed",
    )


def _validate_orders(cr, source_case):
    _assert(
        _fetch_value(
            cr,
            """
            SELECT count(*) = 0
            FROM trucalc_order o
            JOIN trucalc_pass4a2_order_snapshot s ON s.id = o.id
            WHERE o.status IS DISTINCT FROM s.status
               OR o.company_id IS DISTINCT FROM s.company_id
               OR o.assigned_vendor_id IS DISTINCT FROM s.assigned_vendor_id
               OR o.vendor_fee IS DISTINCT FROM s.vendor_fee
               OR o.service_type IS DISTINCT FROM s.service_type
            """,
        ),
        "protected order business history changed",
    )

    if source_case == "known":
        _assert(
            _fetch_value(
                cr,
                """
                SELECT array_agg(id ORDER BY id) = ARRAY[3, 4, 5, 8, 9]
                FROM trucalc_order
                WHERE bidding_round = 1
                  AND id IN (3, 4, 5, 6, 8, 9)
                """,
            ),
            "the wrong legacy orders moved to Round 1",
        )
        _assert(
            _fetch_value(
                cr,
                """
                SELECT bidding_round IS NOT DISTINCT FROM 0
                FROM trucalc_order
                WHERE id = 6
                """,
            ),
            "Order 6 was modified",
        )


def _validate_relationships(cr):
    _assert(
        _fetch_value(
            cr,
            "SELECT count(*) = 0 FROM trucalc_bid WHERE invitation_id IS NULL",
        ),
        "a bid is missing its required invitation",
    )
    _assert(
        _fetch_value(
            cr,
            """
            SELECT count(*) = 0
            FROM trucalc_bid b
            JOIN trucalc_bid_invitation i ON i.id = b.invitation_id
            JOIN trucalc_order o ON o.id = i.order_id
            WHERE b.order_id IS DISTINCT FROM i.order_id
               OR b.vendor_id IS DISTINCT FROM i.vendor_id
               OR b.company_id IS DISTINCT FROM i.company_id
               OR i.company_id IS DISTINCT FROM o.company_id
            """,
        ),
        "bid, invitation, and order ownership does not agree",
    )
    _assert(
        _fetch_value(
            cr,
            """
            SELECT count(*) = 0
            FROM trucalc_bid
            WHERE option_name IS NULL OR btrim(option_name) = ''
            """,
        ),
        "a bid option name is missing or blank",
    )
    _assert(
        _fetch_value(
            cr,
            """
            SELECT count(*) = 0
            FROM trucalc_bid
            WHERE status NOT IN (
                'draft', 'submitted', 'selected', 'not_selected', 'disqualified'
            )
            """,
        ),
        "a bid has a status outside the final selection",
    )


def _validate_known_results(cr):
    _assert(_fetch_value(cr, "SELECT count(*) FROM trucalc_bid") == 11, "bid count is not 11")
    _assert(
        _fetch_value(
            cr,
            "SELECT count(*) FROM trucalc_bid_invitation WHERE is_legacy_reconstructed",
        ) == 10,
        "reconstructed invitation count is not 10",
    )

    cr.execute(
        "SELECT status, count(*) FROM trucalc_bid GROUP BY status ORDER BY status"
    )
    _assert(
        cr.fetchall() == [("not_selected", 4), ("selected", 4), ("submitted", 3)],
        "final bid status distribution is unexpected",
    )

    cr.execute(
        """
        SELECT state, count(*)
        FROM trucalc_bid_invitation
        WHERE is_legacy_reconstructed
        GROUP BY state
        ORDER BY state
        """
    )
    _assert(
        cr.fetchall() == [("closed", 8), ("invited", 2)],
        "reconstructed invitation state distribution is unexpected",
    )

    _assert(
        _fetch_value(
            cr,
            """
            SELECT count(*) = 0
            FROM trucalc_bid_invitation
            WHERE is_legacy_reconstructed
              AND (
                  round_number <> 1
                  OR invited_by IS NOT NULL
                  OR invited_at IS NOT NULL
              )
            """,
        ),
        "reconstructed invitation provenance is incorrect",
    )

    cr.execute(
        """
        SELECT b.id, b.option_name
        FROM trucalc_bid b
        ORDER BY b.id
        """
    )
    _assert(
        cr.fetchall() == [
            (1, "Legacy Option 1"),
            (2, "Legacy Option 1"),
            (3, "Legacy Option 2"),
            (4, "Legacy Option 1"),
            (5, "Legacy Option 1"),
            (6, "Legacy Option 1"),
            (7, "Legacy Option 1"),
            (8, "Legacy Option 1"),
            (9, "Legacy Option 1"),
            (10, "Legacy Option 1"),
            (11, "Legacy Option 1"),
        ],
        "legacy option names are not deterministic",
    )

    cr.execute(
        """
        SELECT array_agg(b.id ORDER BY b.id)
        FROM trucalc_bid b
        JOIN trucalc_bid_invitation i ON i.id = b.invitation_id
        WHERE i.order_id = 8 AND i.vendor_id = 2 AND i.round_number = 1
        """
    )
    _assert(cr.fetchone()[0] == [2, 3], "Bids 2 and 3 do not share one invitation")


def migrate(cr, version):
    cr.execute(
        """
        SELECT
            source_case,
            source_bid_count,
            source_invitation_count,
            source_reconstructed_count
        FROM trucalc_pass4a2_context
        """
    )
    source_case, source_bid_count, source_invitation_count, source_reconstructed_count = cr.fetchone()

    _assert(source_case in ("zero", "known"), "migration source case is invalid")
    _assert(
        _fetch_value(cr, "SELECT count(*) FROM trucalc_bid") == source_bid_count,
        "bid count changed unexpectedly",
    )

    _validate_immutable_bids(cr)
    _validate_orders(cr, source_case)
    _validate_relationships(cr)

    if source_case == "known":
        _validate_known_results(cr)
    else:
        _assert(source_bid_count == 0, "zero case contains source bids")
        _assert(
            _fetch_value(cr, "SELECT count(*) FROM trucalc_bid_invitation")
            == source_invitation_count,
            "zero-case migration changed invitation count",
        )
        _assert(
            _fetch_value(
                cr,
                """
                SELECT count(*)
                FROM trucalc_bid_invitation
                WHERE is_legacy_reconstructed
                """,
            ) == source_reconstructed_count,
            "zero-case migration changed reconstructed invitation count",
        )

    cr.execute("DROP TABLE trucalc_pass4a2_bid_snapshot")
    cr.execute("DROP TABLE trucalc_pass4a2_order_snapshot")
    cr.execute("DROP TABLE trucalc_pass4a2_context")
