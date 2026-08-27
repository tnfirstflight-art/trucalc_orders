def _assert(condition, message):
    if not condition:
        raise RuntimeError(f"Pass 4C.1 migration aborted: {message}")


def migrate(cr, version):
    cr.execute("SELECT to_regclass('trucalc_pass4c1_context') IS NULL")
    _assert(cr.fetchone()[0], "migration context table already exists")
    cr.execute(
        """
        CREATE TABLE trucalc_pass4c1_context AS
        SELECT
            (SELECT count(*) FROM trucalc_order) AS order_count,
            (SELECT count(*) FROM trucalc_bid) AS bid_count,
            (SELECT count(*) FROM trucalc_bid_invitation) AS invitation_count,
            (SELECT count(*) FROM trucalc_order_vendor_authorization) AS authorization_count,
            (SELECT count(*) FROM trucalc_bid_audit) AS bid_audit_count,
            (SELECT jsonb_agg(row_to_json(current_engaged) ORDER BY current_engaged.id)
             FROM (
                SELECT id, order_number, status, company_id, bidding_round,
                       assigned_vendor_id, vendor_fee, vendor_delivery_date,
                       vendor_engaged_at
                FROM trucalc_order WHERE status = 'engaged' ORDER BY id
             ) current_engaged) AS engaged_snapshot,
            (SELECT jsonb_agg(row_to_json(legacy) ORDER BY legacy.id)
             FROM (
                SELECT id, order_number, status, bidding_round, assigned_vendor_id,
                       vendor_fee, vendor_delivery_date, vendor_engaged_at
                FROM trucalc_order
                WHERE status = 'assigned' OR order_number = 'TC-00005'
                ORDER BY id
             ) legacy) AS protected_legacy_snapshot
        """
    )
