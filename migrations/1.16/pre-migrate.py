def _fetch(cr, query, params=None):
    cr.execute(query, params or ())
    return cr.fetchone()[0]


def _assert(condition, message):
    if not condition:
        raise RuntimeError(f"Pass 4D.1 migration aborted: {message}")


def migrate(cr, version):
    _assert(
        _fetch(cr, "SELECT to_regclass('trucalc_pass4d1_context') IS NULL"),
        "migration context table already exists",
    )
    cr.execute("""
        CREATE TABLE trucalc_pass4d1_context AS
        SELECT id, order_number, loan_amount, status, order_date
          FROM trucalc_order
         ORDER BY id
    """)
