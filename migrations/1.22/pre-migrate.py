def migrate(cr, version):
    for table in ('trucalc_order', 'trucalc_order_lifecycle_event',
                  'trucalc_service_area', 'trucalc_negotiated_fee'):
        cr.execute(f'CREATE TABLE trucalc_4e1_snapshot_{table} AS SELECT * FROM {table}')
