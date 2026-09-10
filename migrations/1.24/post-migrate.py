APPROVED_BANK_NAMES = ("Cadence Bank", "Renassant Bank", "First Choice Bank")


def _fetch(cr, query, params=None):
    cr.execute(query, params or ())
    return cr.fetchone()[0]


def _assert(condition, message):
    if not condition:
        raise RuntimeError("Pre-5A A1 post-migration validation failed: %s" % message)


def migrate(cr, version):
    cr.execute("""
        SELECT res_id FROM ir_model_data
         WHERE module = 'base' AND name = 'main_company'
           AND model = 'res.company'
    """)
    main_company_id = cr.fetchone()[0]

    cr.execute("""
        UPDATE res_company
           SET trucalc_is_bank = TRUE,
               trucalc_bank_active = TRUE
         WHERE name = ANY(%s)
    """, (list(APPROVED_BANK_NAMES),))
    _assert(cr.rowcount == 3, "exactly three approved Banks were not classified")
    cr.execute("""
        UPDATE res_company
           SET trucalc_is_bank = FALSE,
               trucalc_bank_active = FALSE
         WHERE id = %s
    """, (main_company_id,))

    # All existing valid Bank identities retain their IDs, partners, and roles;
    # their Odoo company binding is made identical to the authoritative mapping.
    cr.execute("""
        UPDATE res_users
           SET company_id = trucalc_bank_company_id
         WHERE trucalc_bank_company_id IS NOT NULL
           AND company_id IS DISTINCT FROM trucalc_bank_company_id
    """)
    cr.execute("""
        DELETE FROM res_company_users_rel relation
         USING res_users users
         WHERE relation.user_id = users.id
           AND users.trucalc_bank_company_id IS NOT NULL
    """)
    cr.execute("""
        INSERT INTO res_company_users_rel (cid, user_id)
        SELECT trucalc_bank_company_id, id
          FROM res_users
         WHERE trucalc_bank_company_id IS NOT NULL
        ON CONFLICT DO NOTHING
    """)

    # Existing and future internal workflows share the same contract: active,
    # valid Administrator/Operations users receive each operational Bank.
    cr.execute("""
        WITH internal_users AS (
            SELECT users.id
              FROM res_users users
             WHERE users.active
               AND NOT users.share
               AND users.trucalc_bank_company_id IS NULL
               AND users.trucalc_vendor_id IS NULL
               AND (
                    SELECT count(*)
                      FROM res_groups_users_rel rel
                      JOIN ir_model_data data
                        ON data.model = 'res.groups' AND data.res_id = rel.gid
                     WHERE rel.uid = users.id
                       AND data.module = 'trucalc_orders'
                       AND data.name IN ('group_trucalc_admin', 'group_trucalc_operations')
               ) = 1
               AND NOT EXISTS (
                    SELECT 1
                      FROM res_groups_users_rel rel
                      JOIN ir_model_data data
                        ON data.model = 'res.groups' AND data.res_id = rel.gid
                     WHERE rel.uid = users.id
                       AND data.module = 'trucalc_orders'
                       AND data.name IN (
                           'group_bank_admin', 'group_bank_requestor',
                           'group_bank_view_only', 'group_vendor_portal'
                       )
               )
        ), banks AS (
            SELECT id FROM res_company WHERE name = ANY(%s)
        )
        INSERT INTO res_company_users_rel (cid, user_id)
        SELECT banks.id, internal_users.id FROM banks CROSS JOIN internal_users
        ON CONFLICT DO NOTHING
    """, (list(APPROVED_BANK_NAMES),))

    _assert(_fetch(cr, """
        SELECT count(*) = 3 FROM res_company
         WHERE name = ANY(%s)
           AND trucalc_is_bank AND trucalc_bank_active
    """, (list(APPROVED_BANK_NAMES),)), "approved Bank classification is incomplete")
    _assert(_fetch(cr, """
        SELECT NOT trucalc_is_bank AND NOT trucalc_bank_active
          FROM res_company WHERE id = %s
    """, (main_company_id,)), "the TruCalc main company was marked as a Bank")
    _assert(_fetch(cr, """
        SELECT count(*) = 0
          FROM res_users users
          JOIN res_company bank ON bank.id = users.trucalc_bank_company_id
         WHERE users.trucalc_bank_company_id IS NOT NULL
           AND (
                NOT bank.trucalc_is_bank
                OR NOT bank.trucalc_bank_active
                OR bank.id = %s
                OR users.company_id IS DISTINCT FROM bank.id
                OR (SELECT array_agg(cid ORDER BY cid)
                      FROM res_company_users_rel relation
                     WHERE relation.user_id = users.id) IS DISTINCT FROM ARRAY[bank.id]
           )
    """, (main_company_id,)), "an existing Bank user was not bound exactly to its Bank")

    preserved_tables = (
        "trucalc_order", "trucalc_order_lifecycle_event",
        "trucalc_fee_change_request", "trucalc_vendor_deliverable",
        "trucalc_document", "trucalc_document_event",
        "trucalc_service_area", "trucalc_negotiated_fee",
        "trucalc_bank_invoice",
    )
    for table in preserved_tables:
        cr.execute("""SELECT count(*) FROM trucalc_5a1_snapshot_%s old
            FULL JOIN %s current USING (id)
            WHERE old.id IS NULL OR current.id IS NULL
               OR to_jsonb(old) IS DISTINCT FROM to_jsonb(current)""" % (table, table))
        _assert(cr.fetchone()[0] == 0, "%s history changed" % table)

    cr.execute("""SELECT count(*) FROM trucalc_5a1_snapshot_res_company old
        FULL JOIN res_company current USING (id)
        WHERE old.id IS NULL OR current.id IS NULL
           OR to_jsonb(old) IS DISTINCT FROM
              (to_jsonb(current) - ARRAY['trucalc_is_bank', 'trucalc_bank_active'])""")
    _assert(cr.fetchone()[0] == 0, "company identity or profile data changed")
    cr.execute("""SELECT count(*) FROM trucalc_5a1_snapshot_res_users old
        FULL JOIN res_users current USING (id)
        WHERE old.id IS NULL OR current.id IS NULL
           OR (to_jsonb(old) - 'company_id') IS DISTINCT FROM
              (to_jsonb(current) - 'company_id')""")
    _assert(cr.fetchone()[0] == 0, "user identity, persona, or history changed")
    cr.execute("""SELECT count(*) FROM trucalc_5a1_snapshot_group_rel old
        FULL JOIN res_groups_users_rel current USING (gid, uid)
        WHERE old.gid IS NULL OR current.gid IS NULL""")
    _assert(cr.fetchone()[0] == 0, "user group membership changed")
    _assert(_fetch(cr, "SELECT count(*) = 0 FROM trucalc_bank_admin_audit"),
            "migration fabricated Bank administration audit events")

    for table in preserved_tables:
        cr.execute("DROP TABLE trucalc_5a1_snapshot_%s" % table)
    for table in ("res_company", "res_users", "group_rel", "company_rel"):
        cr.execute("DROP TABLE trucalc_5a1_snapshot_%s" % table)
