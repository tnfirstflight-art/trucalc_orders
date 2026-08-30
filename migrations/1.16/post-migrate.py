def _fetch(cr, query, params=None):
    cr.execute(query, params or ())
    return cr.fetchone()[0]


def _assert(condition, message):
    if not condition:
        raise RuntimeError(f"Pass 4D.1 post-migration validation failed: {message}")


def migrate(cr, version):
    _assert(_fetch(cr, """
        SELECT count(*) = 0
          FROM trucalc_pass4d1_context snapshot
          FULL JOIN trucalc_order current USING (id)
         WHERE snapshot.id IS NULL OR current.id IS NULL
            OR snapshot.order_number IS DISTINCT FROM current.order_number
            OR snapshot.loan_amount IS DISTINCT FROM current.loan_amount
            OR snapshot.status IS DISTINCT FROM current.status
            OR snapshot.order_date IS DISTINCT FROM current.order_date
    """), "Order identity, number, Loan Amount, status, or Order Date changed")
    _assert(_fetch(cr, """
        SELECT count(*) = 0
          FROM trucalc_order current
          JOIN trucalc_pass4d1_context snapshot USING (id)
         WHERE current.status = 'draft'
    """), "a historical Order was converted to Draft")
    _assert(_fetch(cr, """
        SELECT count(*) = 0
          FROM trucalc_order current
          JOIN trucalc_pass4d1_context snapshot USING (id)
         WHERE current.county IS NOT NULL
            OR current.service_area_id IS NOT NULL
            OR current.inspection_contact_name IS NOT NULL
            OR current.inspection_contact_phone IS NOT NULL
            OR current.inspection_contact_email IS NOT NULL
    """), "historical Orders received fabricated intake data")
    _assert(
        _fetch(cr, "SELECT count(*) FROM trucalc_service_area") == 0,
        "synthetic Service Areas were created",
    )
    _assert(_fetch(cr, """
        SELECT count(*) = 0
          FROM ir_model_access access
          JOIN ir_model_data data
            ON data.model = 'ir.model.access' AND data.res_id = access.id
         WHERE data.module = 'trucalc_orders'
           AND data.name IN (
               'access_trucalc_order_bank_admin',
               'access_trucalc_order_bank_requestor'
           )
           AND access.perm_create
    """), "raw Bank Order create ACL remains active")
    cr.execute("DROP TABLE trucalc_pass4d1_context")
