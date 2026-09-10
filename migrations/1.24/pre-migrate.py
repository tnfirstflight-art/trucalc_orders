APPROVED_BANK_NAMES = ("Cadence Bank", "Renassant Bank", "First Choice Bank")


def _fetch(cr, query, params=None):
    cr.execute(query, params or ())
    return cr.fetchone()[0]


def _assert(condition, message):
    if not condition:
        raise RuntimeError("Pre-5A A1 pre-migration validation failed: %s" % message)


def migrate(cr, version):
    cr.execute("""
        SELECT res_id FROM ir_model_data
         WHERE module = 'base' AND name = 'main_company'
           AND model = 'res.company'
    """)
    row = cr.fetchone()
    _assert(row, "base.main_company is missing")
    main_company_id = row[0]

    cr.execute("SELECT id, name FROM res_company WHERE name = ANY(%s) ORDER BY name", (list(APPROVED_BANK_NAMES),))
    approved = cr.fetchall()
    _assert(
        sorted(name for _company_id, name in approved) == sorted(APPROVED_BANK_NAMES),
        "the three approved Bank companies were not found exactly once",
    )
    _assert(
        main_company_id not in [company_id for company_id, _name in approved],
        "the TruCalc main company is in the approved Bank set",
    )

    _assert(_fetch(cr, """
        WITH role_ids AS (
            SELECT name, res_id
              FROM ir_model_data
             WHERE module = 'trucalc_orders'
               AND model = 'res.groups'
               AND name IN (
                    'group_trucalc_admin', 'group_trucalc_operations',
                    'group_trucalc_reviewer', 'group_bank_admin',
                    'group_bank_requestor', 'group_bank_view_only',
                    'group_vendor_portal'
               )
        ), persona AS (
            SELECT users.id, users.share, users.trucalc_bank_company_id,
                   users.trucalc_vendor_id,
                   count(*) FILTER (WHERE roles.name IN (
                       'group_bank_admin', 'group_bank_requestor',
                       'group_bank_view_only')) AS bank_roles,
                   bool_or(roles.name IN (
                       'group_trucalc_admin', 'group_trucalc_operations',
                       'group_trucalc_reviewer')) AS internal_role,
                   bool_or(roles.name = 'group_vendor_portal') AS vendor_role,
                   bool_or(rel.gid = base_user.res_id) AS base_user_role
              FROM res_users users
              LEFT JOIN res_groups_users_rel rel ON rel.uid = users.id
              LEFT JOIN role_ids roles ON roles.res_id = rel.gid
              CROSS JOIN LATERAL (
                  SELECT res_id FROM ir_model_data
                   WHERE module = 'base' AND name = 'group_user'
                     AND model = 'res.groups'
              ) base_user
             GROUP BY users.id, users.share, users.trucalc_bank_company_id,
                      users.trucalc_vendor_id
        )
        SELECT count(*) = 0
          FROM persona
         WHERE (bank_roles > 0 OR trucalc_bank_company_id IS NOT NULL)
           AND (
                bank_roles <> 1
                OR trucalc_bank_company_id IS NULL
                OR trucalc_vendor_id IS NOT NULL
                OR internal_role
                OR vendor_role
                OR base_user_role
                OR NOT share
                OR trucalc_bank_company_id NOT IN (
                    SELECT id FROM res_company WHERE name = ANY(%s)
                )
           )
    """, (list(APPROVED_BANK_NAMES),)), "an unexpected mixed or ambiguous Bank identity exists")

    for table in (
        "trucalc_order", "trucalc_order_lifecycle_event",
        "trucalc_fee_change_request", "trucalc_vendor_deliverable",
        "trucalc_document", "trucalc_document_event",
        "trucalc_service_area", "trucalc_negotiated_fee",
        "trucalc_bank_invoice",
    ):
        cr.execute("CREATE TABLE trucalc_5a1_snapshot_%s AS SELECT * FROM %s" % (table, table))
    cr.execute("CREATE TABLE trucalc_5a1_snapshot_res_company AS SELECT * FROM res_company")
    cr.execute("CREATE TABLE trucalc_5a1_snapshot_res_users AS SELECT * FROM res_users")
    cr.execute("CREATE TABLE trucalc_5a1_snapshot_group_rel AS SELECT * FROM res_groups_users_rel")
    cr.execute("CREATE TABLE trucalc_5a1_snapshot_company_rel AS SELECT * FROM res_company_users_rel")
