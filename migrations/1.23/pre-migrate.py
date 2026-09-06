def migrate(cr, version):
    # Preserve complete canonical rows, not a hand-selected subset of pricing fields.
    for table in ('trucalc_order', 'trucalc_order_lifecycle_event', 'trucalc_fee_change_request',
                  'trucalc_vendor_deliverable', 'trucalc_document', 'trucalc_document_event',
                  'trucalc_service_area', 'trucalc_negotiated_fee'):
        cr.execute(f'CREATE TABLE trucalc_4e2_snapshot_{table} AS SELECT * FROM {table}')
