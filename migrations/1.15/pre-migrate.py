def _fetch(cr, query, params=None):
    cr.execute(query, params or ())
    return cr.fetchone()[0]


def _assert(condition, message):
    if not condition:
        raise RuntimeError(f"Pass 4D.0 migration aborted: {message}")


def migrate(cr, version):
    _assert(
        _fetch(cr, "SELECT to_regclass('trucalc_pass4d0_context') IS NULL"),
        "migration context table already exists",
    )
    cr.execute("""
        CREATE TABLE trucalc_pass4d0_context AS
        SELECT id, status, reviewer_id, review_fee, company_id
        FROM trucalc_order
        ORDER BY id
    """)
