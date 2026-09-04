def _fetch(cr, query, params=None):
    cr.execute(query, params or ())
    return cr.fetchone()[0]


def _assert(condition, message):
    if not condition:
        raise RuntimeError(f"Pass 4E.0 post-migration validation failed: {message}")


def migrate(cr, version):
    _assert(_fetch(cr, """
        SELECT count(*) = 0
          FROM trucalc_pass4e0_area_context snapshot
          FULL JOIN trucalc_service_area current USING (id)
         WHERE snapshot.id IS NULL OR current.id IS NULL
            OR snapshot.state_id IS DISTINCT FROM current.state_id
            OR snapshot.county IS DISTINCT FROM current.county
            OR snapshot.county_normalized IS DISTINCT FROM current.county_normalized
            OR snapshot.service_type IS DISTINCT FROM current.service_type
            OR snapshot.active IS DISTINCT FROM current.active
    """), "Service Area identity or activity changed")
    _assert(_fetch(cr, """
        SELECT count(*) = 0 FROM trucalc_service_area
         WHERE base_fee IS NOT NULL
    """), "Base Fees were fabricated")
    _assert(_fetch(cr, """
        SELECT count(*) = 0 FROM trucalc_service_area area
          JOIN res_currency currency ON currency.id = area.currency_id
         WHERE currency.name != 'USD'
    """), "Service Area currency is not USD")
    _assert(_fetch(cr, """
        SELECT count(*) = 0 FROM trucalc_negotiated_fee
    """), "Negotiated Fees were fabricated")
    _assert(_fetch(cr, """
        SELECT count(*) = 0
          FROM trucalc_pass4e0_order_context snapshot
          FULL JOIN trucalc_order current USING (id)
         WHERE snapshot.id IS NULL OR current.id IS NULL
            OR to_jsonb(snapshot) IS DISTINCT FROM
               (to_jsonb(current) - ARRAY[
                    'agreed_fee', 'fee_source', 'negotiated_fee_id',
                    'fee_locked_at', 'fee_currency_id', 'pricing_state_id',
                    'pricing_county_area_id'
               ])
    """), "historical Order data changed")
    _assert(_fetch(cr, """
        SELECT count(*) = 0 FROM trucalc_order
         WHERE agreed_fee IS NOT NULL OR fee_source IS NOT NULL
            OR negotiated_fee_id IS NOT NULL OR fee_locked_at IS NOT NULL
            OR fee_currency_id IS NOT NULL
            OR pricing_state_id IS NOT NULL OR pricing_county_area_id IS NOT NULL
    """), "historical Order fee snapshots were fabricated")
    _assert(_fetch(cr, """
        SELECT count(*) = 0
          FROM trucalc_pass4e0_event_context snapshot
          FULL JOIN trucalc_order_lifecycle_event current USING (id)
         WHERE snapshot.id IS NULL OR current.id IS NULL
            OR to_jsonb(snapshot) IS DISTINCT FROM
               (to_jsonb(current) - ARRAY[
                    'agreed_fee', 'fee_source', 'service_area_id',
                    'negotiated_fee_id', 'fee_currency_id', 'fee_locked_at'
               ])
    """), "historical lifecycle event data changed")
    _assert(_fetch(cr, """
        SELECT count(*) = 0 FROM trucalc_order_lifecycle_event
         WHERE agreed_fee IS NOT NULL OR fee_source IS NOT NULL
            OR service_area_id IS NOT NULL OR negotiated_fee_id IS NOT NULL
            OR fee_currency_id IS NOT NULL OR fee_locked_at IS NOT NULL
    """), "historical fee provenance was fabricated")
    cr.execute("DROP TABLE trucalc_pass4e0_event_context")
    cr.execute("DROP TABLE trucalc_pass4e0_order_context")
    cr.execute("DROP TABLE trucalc_pass4e0_area_context")
