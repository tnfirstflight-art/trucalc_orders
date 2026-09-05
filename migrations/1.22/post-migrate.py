def migrate(cr, version):
    cr.execute('''UPDATE trucalc_order SET current_agreed_fee = agreed_fee,
                  current_fee_change_request_id = NULL, fee_workflow_revision = 0
                  WHERE fee_locked_at IS NOT NULL AND agreed_fee IS NOT NULL
                    AND fee_currency_id IS NOT NULL AND service_area_id IS NOT NULL
                    AND fee_source IN ('base', 'negotiated')''')
    cr.execute('''UPDATE trucalc_order SET current_agreed_fee = NULL,
                  current_fee_change_request_id = NULL, fee_workflow_revision = 0
                  WHERE fee_locked_at IS NULL''')
    excluded = {
        'trucalc_order': ['current_agreed_fee', 'current_fee_change_request_id', 'fee_workflow_revision'],
        'trucalc_order_lifecycle_event': ['fee_change_request_id'],
        'trucalc_service_area': [], 'trucalc_negotiated_fee': [],
    }
    for table, columns in excluded.items():
        cr.execute(f'''SELECT count(*) FROM trucalc_4e1_snapshot_{table} old
            FULL JOIN {table} current USING (id)
            WHERE old.id IS NULL OR current.id IS NULL
               OR to_jsonb(old) IS DISTINCT FROM (to_jsonb(current) - %s::text[])''', (columns,))
        if cr.fetchone()[0]:
            raise RuntimeError(f'4E.1 preservation failed: {table}')
    cr.execute('''SELECT count(*) FROM trucalc_order WHERE
        (fee_locked_at IS NOT NULL AND current_agreed_fee IS DISTINCT FROM agreed_fee)
        OR (fee_locked_at IS NULL AND current_agreed_fee IS NOT NULL)
        OR current_fee_change_request_id IS NOT NULL OR fee_workflow_revision <> 0''')
    if cr.fetchone()[0]:
        raise RuntimeError('4E.1 current fee migration failed')
    cr.execute('SELECT count(*) FROM trucalc_fee_change_request')
    if cr.fetchone()[0]:
        raise RuntimeError('4E.1 migration fabricated requests')
    for table in excluded:
        cr.execute(f'DROP TABLE trucalc_4e1_snapshot_{table}')
