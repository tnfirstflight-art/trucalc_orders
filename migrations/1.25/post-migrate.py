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


CORRECTION_EVENT_COLUMNS = (
    "correction_old_state_id",
    "correction_old_state",
    "correction_old_county",
    "correction_old_service_area_id",
    "correction_old_service_area",
    "correction_new_state_id",
    "correction_new_state",
    "correction_new_county",
    "correction_new_service_area_id",
    "correction_new_service_area",
    "correction_service_type",
    "correction_original_service_area_id",
    "correction_original_service_area",
    "correction_current_fee",
    "correction_schedule_fee",
    "correction_fee_source",
    "correction_negotiated_fee_id",
    "correction_currency_id",
    "correction_reason",
)


def _assert(condition, message):
    if not condition:
        raise RuntimeError(
            "Pre-5A Location Correction A post-migration validation failed: %s"
            % message
        )


def migrate(cr, version):
    cr.execute("""
        UPDATE trucalc_order
           SET original_service_area_id = service_area_id
         WHERE fee_locked_at IS NOT NULL
           AND original_service_area_id IS NULL
    """)
    cr.execute("""
        SELECT count(*) = 0
          FROM trucalc_order
         WHERE (fee_locked_at IS NOT NULL AND original_service_area_id IS NULL)
            OR (fee_locked_at IS NULL AND original_service_area_id IS NOT NULL)
    """)
    _assert(cr.fetchone()[0], "original Service Area provenance is incomplete")

    for table in PRESERVED_TABLES:
        snapshot = "trucalc_5a_location_a_snapshot_%s" % table
        if table == "trucalc_order":
            current_json = "to_jsonb(current) - 'original_service_area_id'"
        elif table == "trucalc_order_lifecycle_event":
            columns = ", ".join("'%s'" % column for column in CORRECTION_EVENT_COLUMNS)
            current_json = "to_jsonb(current) - ARRAY[%s]" % columns
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

    cr.execute("""
        SELECT count(*) = 0
          FROM trucalc_order_lifecycle_event
         WHERE event_type = 'property_location_corrected'
    """)
    _assert(cr.fetchone()[0], "migration fabricated location correction events")

    for table in PRESERVED_TABLES:
        cr.execute("DROP TABLE trucalc_5a_location_a_snapshot_%s" % table)
