def _assert(condition, message):
    if not condition:
        raise RuntimeError(f"Pass 4B.2C.2 migration aborted: {message}")


def migrate(cr, version):
    cr.execute(
        """
        SELECT vendor_id, service_type, array_agg(id ORDER BY id),
               array_agg(fee ORDER BY fee), count(*)
        FROM trucalc_vendor_fee
        GROUP BY vendor_id, service_type
        HAVING count(*) > 1
        ORDER BY vendor_id, service_type
        """
    )
    duplicates = cr.fetchall()
    _assert(len(duplicates) <= 1, "an unresolved Vendor/service fee duplicate exists")
    jimmy_cleanup = bool(duplicates)
    if jimmy_cleanup:
        vendor_id, service_type, fee_ids, fees, count = duplicates[0]
        cr.execute("SELECT name FROM trucalc_vendor WHERE id = %s", (vendor_id,))
        vendor_name = cr.fetchone()[0]
        _assert(
            vendor_name == "Jimmy Vandergrift"
            and service_type == "evaluation"
            and count == 2
            and [float(fee) for fee in fees] == [75.0, 100.0],
            "the duplicate does not match the owner-approved Jimmy Evaluation cleanup",
        )

    cr.execute(
        """
        SELECT c.conrelid::regclass::text, a.attname
        FROM pg_constraint c
        JOIN unnest(c.conkey) WITH ORDINALITY AS key(attnum, ordinality) ON TRUE
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = key.attnum
        WHERE c.contype = 'f' AND c.confrelid = 'trucalc_vendor_fee'::regclass
        """
    )
    _assert(not cr.fetchall(), "Vendor Fee rows have unexpected foreign-key references")

    if jimmy_cleanup:
        cr.execute("SELECT id FROM trucalc_vendor_fee WHERE vendor_id = %s AND service_type = %s AND fee = 75", (vendor_id, service_type))
        authoritative = cr.fetchall()
        cr.execute("SELECT id FROM trucalc_vendor_fee WHERE vendor_id = %s AND service_type = %s AND fee = 100", (vendor_id, service_type))
        duplicate = cr.fetchall()
        _assert(len(authoritative) == 1 and len(duplicate) == 1, "Jimmy fee rows are not uniquely identifiable")

    cr.execute("SELECT to_regclass('trucalc_pass4b2c2_context') IS NULL")
    _assert(cr.fetchone()[0], "migration context table already exists")
    cr.execute(
        """
        CREATE TABLE trucalc_pass4b2c2_context AS
        SELECT
            (SELECT count(*) FROM trucalc_vendor) AS vendor_count,
            (SELECT count(*) FROM res_users WHERE trucalc_vendor_id IS NOT NULL) AS portal_mapping_count,
            (SELECT count(*) FROM trucalc_order) AS order_count,
            (SELECT count(*) FROM trucalc_bid) AS bid_count,
            (SELECT count(*) FROM trucalc_bid_invitation) AS invitation_count,
            (SELECT count(*) FROM trucalc_order_vendor_authorization) AS authorization_count,
            %s::boolean AS jimmy_duplicate_cleaned
        """
        , (jimmy_cleanup,)
    )
    if jimmy_cleanup:
        cr.execute(
            """
            CREATE TABLE trucalc_pass4b2c2_ambiguous_invitation AS
            SELECT i.id
            FROM trucalc_bid_invitation i
            WHERE i.vendor_id = %s
              AND EXISTS (
                  SELECT 1 FROM trucalc_order o
                  WHERE o.id = i.order_id AND o.service_type = %s
              )
            """,
            (vendor_id, service_type),
        )
    else:
        cr.execute("CREATE TABLE trucalc_pass4b2c2_ambiguous_invitation (id integer PRIMARY KEY)")
    if jimmy_cleanup:
        cr.execute("DELETE FROM trucalc_vendor_fee WHERE id = %s", (duplicate[0][0],))
        _assert(cr.rowcount == 1, "the approved $100 Jimmy Evaluation fee was not removed")
