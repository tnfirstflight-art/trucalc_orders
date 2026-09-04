def _fetch(cr, query, params=None):
    cr.execute(query, params or ())
    return cr.fetchone()[0]


def _assert(condition, message):
    if not condition:
        raise RuntimeError(f"Pass 4E.0 migration aborted: {message}")


def migrate(cr, version):
    for table in (
        "trucalc_pass4e0_area_context",
        "trucalc_pass4e0_order_context",
        "trucalc_pass4e0_event_context",
    ):
        _assert(
            _fetch(cr, "SELECT to_regclass(%s) IS NULL", (table,)),
            f"migration context table {table} already exists",
        )
    cr.execute("""
        CREATE TABLE trucalc_pass4e0_area_context AS
        SELECT id, state_id, county, county_normalized, service_type, active
          FROM trucalc_service_area ORDER BY id
    """)
    cr.execute("""
        CREATE TABLE trucalc_pass4e0_order_context AS
        SELECT * FROM trucalc_order ORDER BY id
    """)
    cr.execute("""
        CREATE TABLE trucalc_pass4e0_event_context AS
        SELECT * FROM trucalc_order_lifecycle_event ORDER BY id
    """)
