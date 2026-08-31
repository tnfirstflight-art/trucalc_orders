def _fetch(cr, query, params=None):
    cr.execute(query, params or ())
    return cr.fetchone()[0]


def _assert(condition, message):
    if not condition:
        raise RuntimeError(f"Pass 4D.2 migration aborted: {message}")


def migrate(cr, version):
    _assert(
        _fetch(cr, "SELECT to_regclass('trucalc_pass4d2_context') IS NULL"),
        "migration context table already exists",
    )
    cr.execute("""
        CREATE TABLE trucalc_pass4d2_context AS
        SELECT id, order_number, status, order_date, loan_amount, county,
               service_area_id, inspection_contact_name,
               inspection_contact_phone, inspection_contact_email
          FROM trucalc_order
         ORDER BY id
    """)
    cr.execute("""
        CREATE TABLE trucalc_pass4d2_event_context AS
        SELECT id, order_id, event_type, from_status, to_status, actor_id, event_at
          FROM trucalc_order_lifecycle_event
         ORDER BY id
    """)
