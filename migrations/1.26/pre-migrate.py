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
            "Pre-5A Location Correction B pre-migration validation failed: %s"
            % message
        )


def migrate(cr, version):
    for table in PRESERVED_TABLES:
        snapshot = "trucalc_5a_location_b_snapshot_%s" % table
        cr.execute("SELECT to_regclass(%s)", (snapshot,))
        _assert(not cr.fetchone()[0], "migration snapshot already exists: %s" % snapshot)
        cr.execute("CREATE TABLE %s AS SELECT * FROM %s" % (snapshot, table))
