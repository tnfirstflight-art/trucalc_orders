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
            "Pre-5A Location Correction B post-migration validation failed: %s"
            % message
        )


def migrate(cr, version):
    for table in PRESERVED_TABLES:
        snapshot = "trucalc_5a_location_b_snapshot_%s" % table
        if table == "trucalc_order_lifecycle_event":
            current_json = "to_jsonb(current) - 'location_correction_request_id'"
        elif table == "trucalc_fee_change_request":
            current_json = "to_jsonb(current) - 'location_correction_request_id'"
        else:
            current_json = "to_jsonb(current)"
        cr.execute("""
            SELECT count(*)
              FROM {snapshot} old
              FULL JOIN {table} current USING (id)
             WHERE old.id IS NULL OR current.id IS NULL
                OR to_jsonb(old) IS DISTINCT FROM {current_json}
        """.format(
            snapshot=snapshot, table=table, current_json=current_json,
        ))
        _assert(cr.fetchone()[0] == 0, "%s business history changed" % table)

    cr.execute("SELECT count(*) FROM trucalc_location_correction_request")
    _assert(cr.fetchone()[0] == 0, "migration fabricated location correction requests")
    cr.execute("""
        SELECT count(*)
          FROM trucalc_order_lifecycle_event
         WHERE event_type IN (
            'property_location_correction_requested',
            'property_location_correction_fee_declined'
         )
            OR location_correction_request_id IS NOT NULL
    """)
    _assert(cr.fetchone()[0] == 0, "migration fabricated Location Correction B events")
    cr.execute("""
        SELECT count(*)
          FROM trucalc_fee_change_request
         WHERE location_correction_request_id IS NOT NULL
    """)
    _assert(cr.fetchone()[0] == 0, "migration fabricated Fee Change linkage")

    for table in PRESERVED_TABLES:
        cr.execute("DROP TABLE trucalc_5a_location_b_snapshot_%s" % table)
