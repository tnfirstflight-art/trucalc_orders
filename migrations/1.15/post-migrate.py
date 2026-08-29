def _fetch(cr, query, params=None):
    cr.execute(query, params or ())
    return cr.fetchone()[0]


def _assert(condition, message):
    if not condition:
        raise RuntimeError(f"Pass 4D.0 post-migration validation failed: {message}")


def migrate(cr, version):
    _assert(_fetch(cr, """
        SELECT count(*)=0
        FROM trucalc_pass4d0_context snapshot
        FULL JOIN trucalc_order current USING (id)
        WHERE snapshot.id IS NULL OR current.id IS NULL
           OR snapshot.status IS DISTINCT FROM current.status
           OR snapshot.reviewer_id IS DISTINCT FROM current.reviewer_id
           OR snapshot.review_fee IS DISTINCT FROM current.review_fee
           OR snapshot.company_id IS DISTINCT FROM current.company_id
    """), "Order identity, status, reviewer, fee, or company changed")
    _assert(
        _fetch(cr, "SELECT count(*) FROM trucalc_order_lifecycle_event") == 0,
        "synthetic lifecycle events were created",
    )
    _assert(_fetch(cr, """
        SELECT count(*)=0 FROM trucalc_order WHERE reviewer_user_id IS NOT NULL
    """), "existing Orders received an unexpected Reviewer user")
    cr.execute("""
        UPDATE res_users users
           SET action_id = home_action.res_id,
               write_date = now()
          FROM ir_model_data reviewer_group,
               ir_model_data home_action
         WHERE reviewer_group.module = 'trucalc_orders'
           AND reviewer_group.name = 'group_trucalc_reviewer'
           AND home_action.module = 'trucalc_orders'
           AND home_action.name = 'action_trucalc_orders'
           AND EXISTS (
               SELECT 1
                 FROM res_groups_users_rel membership
                WHERE membership.uid = users.id
                  AND membership.gid = reviewer_group.res_id
           )
           AND users.active
           AND NOT users.share
           AND users.trucalc_bank_company_id IS NULL
           AND users.trucalc_vendor_id IS NULL
           AND users.action_id IS DISTINCT FROM home_action.res_id
    """)
    cr.execute("DROP TABLE trucalc_pass4d0_context")
