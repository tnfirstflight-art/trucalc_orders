PRESERVED_TABLES = (
    "trucalc_order",
    "trucalc_order_lifecycle_event",
    "trucalc_fee_change_request",
    "trucalc_vendor_deliverable",
    "trucalc_document",
    "trucalc_document_event",
    "trucalc_service_area",
    "trucalc_negotiated_fee",
    "trucalc_bank_invoice",
    "trucalc_bid",
    "trucalc_bid_invitation",
    "trucalc_order_vendor_authorization",
    "trucalc_vendor_engagement",
)


def _assert(condition, message):
    if not condition:
        raise RuntimeError(
            "Pre-5A Internal UX Pass B post-migration validation failed: %s"
            % message
        )


def migrate(cr, version):
    cr.execute("""
        SELECT count(*)
          FROM trucalc_order
         WHERE review_due_date IS NOT NULL
    """)
    _assert(
        cr.fetchone()[0] == 0,
        "migration fabricated historical Review Due Dates",
    )

    for table in PRESERVED_TABLES:
        snapshot = "trucalc_5a_ux_b_snapshot_%s" % table
        current_json = (
            "to_jsonb(current) - 'review_due_date'"
            if table == "trucalc_order"
            else "to_jsonb(current)"
        )
        cr.execute("""
            SELECT count(*)
              FROM {snapshot} old
              FULL JOIN {table} current USING (id)
             WHERE old.id IS NULL OR current.id IS NULL
                OR to_jsonb(old) IS DISTINCT FROM {current_json}
        """.format(
            snapshot=snapshot,
            table=table,
            current_json=current_json,
        ))
        _assert(cr.fetchone()[0] == 0, "%s business history changed" % table)

    for table in PRESERVED_TABLES:
        cr.execute("DROP TABLE trucalc_5a_ux_b_snapshot_%s" % table)
