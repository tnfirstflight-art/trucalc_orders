def _assert(condition, message):
    if not condition:
        raise RuntimeError(f"Pass 4C.0 migration aborted: {message}")


def migrate(cr, version):
    cr.execute("SELECT to_regclass('trucalc_pass4c0_context') IS NULL")
    _assert(cr.fetchone()[0], "migration context table already exists")
    cr.execute(
        """
        CREATE TABLE trucalc_pass4c0_context AS
        SELECT
            (SELECT count(*) FROM trucalc_order) AS order_count,
            (SELECT count(*) FROM trucalc_bid) AS bid_count,
            (SELECT count(*) FROM trucalc_bid_invitation) AS invitation_count,
            (SELECT count(*) FROM trucalc_order_vendor_authorization) AS authorization_count,
            (SELECT row_to_json(legacy) FROM (
                SELECT o.status, o.bidding_round, o.assigned_vendor_id,
                       b.status AS bid_status, b.response_type,
                       b.proposed_delivery_date, i.is_legacy_reconstructed
                FROM trucalc_order o
                JOIN trucalc_bid b ON b.order_id = o.id
                JOIN trucalc_bid_invitation i ON i.id = b.invitation_id
                WHERE o.order_number = 'TC-00005' AND b.id = 11
            ) legacy) AS tc00005_bid11
        """
    )
