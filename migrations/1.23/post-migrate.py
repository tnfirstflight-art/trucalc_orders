def migrate(cr, version):
    tables = ('trucalc_order', 'trucalc_order_lifecycle_event', 'trucalc_fee_change_request',
              'trucalc_vendor_deliverable', 'trucalc_document', 'trucalc_document_event',
              'trucalc_service_area', 'trucalc_negotiated_fee')
    for table in tables:
        excluded = ['bank_invoice_id'] if table == 'trucalc_order_lifecycle_event' else []
        cr.execute(f'''SELECT count(*) FROM trucalc_4e2_snapshot_{table} old
            FULL JOIN {table} current USING (id)
            WHERE old.id IS NULL OR current.id IS NULL
               OR to_jsonb(old) IS DISTINCT FROM (to_jsonb(current) - %s::text[])''', (excluded,))
        if cr.fetchone()[0]:
            raise RuntimeError(f'4E.2 preservation failed: {table}')
    cr.execute('SELECT count(*) FROM trucalc_bank_invoice')
    if cr.fetchone()[0]:
        raise RuntimeError('4E.2 migration fabricated invoices')
    cr.execute('SELECT count(*) FROM trucalc_order_lifecycle_event WHERE bank_invoice_id IS NOT NULL')
    if cr.fetchone()[0]:
        raise RuntimeError('4E.2 migration fabricated invoice events')
    for table in tables:
        cr.execute(f'DROP TABLE trucalc_4e2_snapshot_{table}')
