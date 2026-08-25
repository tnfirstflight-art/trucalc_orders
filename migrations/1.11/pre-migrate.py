def _assert(condition, message):
    if not condition:
        raise RuntimeError(f"Pass 4B.2C.3 migration aborted: {message}")


def migrate(cr, version):
    cr.execute("SELECT to_regclass('trucalc_pass4b2c3_context') IS NULL")
    _assert(cr.fetchone()[0], "migration context table already exists")
    cr.execute(
        """
        CREATE TABLE trucalc_pass4b2c3_context AS
        SELECT
            (SELECT count(*) FROM trucalc_order) AS order_count,
            (SELECT count(*) FROM trucalc_bid_invitation) AS invitation_count,
            (SELECT count(*) FROM trucalc_order_vendor_authorization) AS authorization_count,
            (SELECT count(*) FROM trucalc_bid) AS bid_count,
            (SELECT count(*) FROM trucalc_vendor_fee) AS fee_count
        """
    )
    cr.execute("SELECT count(*) FROM trucalc_bid WHERE invitation_id IS NULL")
    _assert(cr.fetchone()[0] == 0, "an existing Bid lacks invitation provenance")
    cr.execute("SELECT array_agg(id ORDER BY id) FROM trucalc_bid WHERE invitation_id = 8")
    _assert(cr.fetchone()[0] == [2, 3], "legacy Invitation 8 no longer contains Bids 2 and 3")
