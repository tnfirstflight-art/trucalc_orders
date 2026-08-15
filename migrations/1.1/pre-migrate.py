KNOWN_BID_STRUCTURE = [
    (1, 8, 1, "rejected"),
    (2, 8, 2, "rejected"),
    (3, 8, 2, "selected"),
    (4, 5, 1, "selected"),
    (5, 5, 2, "rejected"),
    (6, 4, 1, "submitted"),
    (7, 4, 2, "submitted"),
    (8, 3, 1, "selected"),
    (9, 3, 2, "rejected"),
    (10, 9, 1, "submitted"),
    (11, 9, 2, "awarded"),
]

KNOWN_ORDER_IDS = [3, 4, 5, 8, 9]


def _fetch_value(cr, query, params=None):
    cr.execute(query, params or ())
    return cr.fetchone()[0]


def _assert(condition, message):
    if not condition:
        raise RuntimeError(f"Pass 4A.2 migration aborted: {message}")


def _create_snapshots(cr, source_case):
    _assert(
        _fetch_value(
            cr,
            "SELECT to_regclass('trucalc_pass4a2_bid_snapshot') IS NULL",
        ),
        "temporary bid snapshot table already exists",
    )
    _assert(
        _fetch_value(
            cr,
            "SELECT to_regclass('trucalc_pass4a2_order_snapshot') IS NULL",
        ),
        "temporary order snapshot table already exists",
    )
    _assert(
        _fetch_value(
            cr,
            "SELECT to_regclass('trucalc_pass4a2_context') IS NULL",
        ),
        "temporary migration context table already exists",
    )

    cr.execute(
        """
        CREATE TABLE trucalc_pass4a2_bid_snapshot AS
        SELECT
            id,
            order_id,
            vendor_id,
            company_id,
            bid_amount,
            turn_time_days,
            notes,
            create_uid,
            create_date
        FROM trucalc_bid
        """
    )
    cr.execute(
        """
        CREATE TABLE trucalc_pass4a2_order_snapshot AS
        SELECT
            id,
            status,
            company_id,
            assigned_vendor_id,
            vendor_fee,
            bidding_round,
            service_type
        FROM trucalc_order
        WHERE id IN (3, 4, 5, 6, 8, 9)
        """
    )
    cr.execute(
        """
        CREATE TABLE trucalc_pass4a2_context (
            source_case varchar NOT NULL,
            source_bid_count integer NOT NULL,
            source_invitation_count integer NOT NULL,
            source_reconstructed_count integer NOT NULL
        )
        """
    )
    cr.execute(
        """
        INSERT INTO trucalc_pass4a2_context (
            source_case,
            source_bid_count,
            source_invitation_count,
            source_reconstructed_count
        )
        SELECT %s, %s, count(*),
               count(*) FILTER (WHERE is_legacy_reconstructed)
        FROM trucalc_bid_invitation
        """,
        (source_case, len(KNOWN_BID_STRUCTURE) if source_case == "known" else 0),
    )


def _validate_known_source(cr):
    cr.execute(
        """
        SELECT id, order_id, vendor_id, status
        FROM trucalc_bid
        ORDER BY id
        """
    )
    _assert(cr.fetchall() == KNOWN_BID_STRUCTURE, "legacy bid structure is unexpected")

    _assert(
        _fetch_value(
            cr,
            """
            SELECT count(*) = 0
            FROM trucalc_bid
            WHERE invitation_id IS NOT NULL OR option_name IS NOT NULL
            """,
        ),
        "legacy bids already contain invitation links or option names",
    )
    _assert(
        _fetch_value(cr, "SELECT count(*) = 0 FROM trucalc_bid_invitation"),
        "invitation records already exist for the known legacy dataset",
    )
    _assert(
        _fetch_value(
            cr,
            """
            SELECT array_agg(DISTINCT order_id ORDER BY order_id) = %s::integer[]
            FROM trucalc_bid
            """,
            (KNOWN_ORDER_IDS,),
        ),
        "the set of bid-bearing orders is unexpected",
    )
    _assert(
        _fetch_value(
            cr,
            """
            SELECT count(*) = 0
            FROM trucalc_bid b
            JOIN trucalc_order o ON o.id = b.order_id
            WHERE b.company_id IS DISTINCT FROM o.company_id
            """,
        ),
        "a legacy bid company differs from its order company",
    )
    _assert(
        _fetch_value(
            cr,
            """
            SELECT count(*) = 0
            FROM trucalc_order
            WHERE id = ANY(%s::integer[]) AND bidding_round IS DISTINCT FROM 0
            """,
            (KNOWN_ORDER_IDS,),
        ),
        "a bid-bearing legacy order is not at bidding round zero",
    )


def _migrate_known_source(cr):
    cr.execute(
        """
        UPDATE trucalc_order
        SET bidding_round = 1
        WHERE id IN (
            SELECT DISTINCT order_id
            FROM trucalc_bid
        )
        """
    )
    _assert(cr.rowcount == 5, "expected exactly five orders to move to Round 1")

    cr.execute(
        """
        INSERT INTO trucalc_bid_invitation (
            order_id,
            vendor_id,
            company_id,
            round_number,
            state,
            is_legacy_reconstructed,
            invited_by,
            invited_at,
            declined_at,
            revoked_at,
            create_uid,
            create_date,
            write_uid,
            write_date
        )
        SELECT
            b.order_id,
            b.vendor_id,
            o.company_id,
            1,
            CASE WHEN o.status = 'bid_requested' THEN 'invited' ELSE 'closed' END,
            TRUE,
            NULL,
            NULL,
            NULL,
            NULL,
            1,
            NOW(),
            1,
            NOW()
        FROM trucalc_bid b
        JOIN trucalc_order o ON o.id = b.order_id
        GROUP BY b.order_id, b.vendor_id, o.company_id, o.status
        ORDER BY b.order_id, b.vendor_id
        """
    )
    _assert(cr.rowcount == 10, "expected exactly ten reconstructed invitations")

    cr.execute(
        """
        UPDATE trucalc_bid b
        SET invitation_id = i.id
        FROM trucalc_bid_invitation i
        WHERE i.is_legacy_reconstructed
          AND i.round_number = 1
          AND i.order_id = b.order_id
          AND i.vendor_id = b.vendor_id
          AND b.invitation_id IS NULL
        """
    )
    _assert(cr.rowcount == 11, "expected exactly eleven bids to be linked")

    cr.execute(
        """
        WITH numbered_options AS (
            SELECT
                id,
                row_number() OVER (
                    PARTITION BY invitation_id
                    ORDER BY create_date, id
                ) AS option_number
            FROM trucalc_bid
        )
        UPDATE trucalc_bid b
        SET option_name = 'Legacy Option ' || n.option_number
        FROM numbered_options n
        WHERE n.id = b.id
        """
    )
    _assert(cr.rowcount == 11, "expected exactly eleven option names")

    cr.execute(
        """
        UPDATE trucalc_bid
        SET status = CASE status
            WHEN 'rejected' THEN 'not_selected'
            WHEN 'awarded' THEN 'selected'
            ELSE status
        END
        WHERE status IN ('rejected', 'awarded')
        """
    )
    _assert(cr.rowcount == 5, "expected exactly five legacy status conversions")

    _assert(
        _fetch_value(
            cr,
            "SELECT count(*) = 0 FROM trucalc_bid WHERE invitation_id IS NULL",
        ),
        "a migrated bid is missing its invitation",
    )
    _assert(
        _fetch_value(
            cr,
            """
            SELECT count(*) = 0
            FROM trucalc_bid b
            JOIN trucalc_bid_invitation i ON i.id = b.invitation_id
            WHERE b.order_id IS DISTINCT FROM i.order_id
               OR b.vendor_id IS DISTINCT FROM i.vendor_id
               OR b.company_id IS DISTINCT FROM i.company_id
            """,
        ),
        "a migrated bid does not agree with its invitation ownership",
    )


def migrate(cr, version):
    bid_count = _fetch_value(cr, "SELECT count(*) FROM trucalc_bid")
    source_case = "zero" if bid_count == 0 else "known"

    if bid_count:
        _assert(
            bid_count == len(KNOWN_BID_STRUCTURE),
            f"unexpected nonzero legacy population ({bid_count} bids)",
        )
        _validate_known_source(cr)

    _create_snapshots(cr, source_case)

    if source_case == "known":
        _migrate_known_source(cr)
