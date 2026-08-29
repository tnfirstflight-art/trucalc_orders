def _fetch(cr, query, params=None):
    cr.execute(query, params or ())
    return cr.fetchone()[0]


def _assert(condition, message):
    if not condition:
        raise RuntimeError(f"Pass 4C.2 migration aborted: {message}")


def migrate(cr, version):
    _assert(_fetch(cr, "SELECT to_regclass('trucalc_pass4c2_context') IS NULL"),
            "migration context table already exists")
    cr.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name='trucalc_document'
        ORDER BY column_name
    """)
    columns = tuple(row[0] for row in cr.fetchall())
    _assert(columns == (
        'company_id', 'create_date', 'create_uid', 'document_type', 'filename', 'id',
        'name', 'order_id', 'upload_date', 'uploaded_by', 'write_date', 'write_uid',
    ),
            "legacy document structure is unexpected")
    cr.execute("""
        SELECT id, filename, document_type FROM trucalc_document ORDER BY id
    """)
    _assert(cr.fetchall() == [
        (1, 'Appraisal.pdf', 'appraisal'),
        (3, 'Appendix H-1 UAD Compliance Rules-URAR 08012025.xlsx', 'other'),
        (4, 'Doc1.pdf', 'other'),
    ], "legacy document data differs from the approved three-record mapping")
    _assert(_fetch(cr, """
        SELECT count(*)=0 FROM (
            SELECT order_id, lower(filename) FROM trucalc_document
            GROUP BY order_id, lower(filename) HAVING count(*) > 1
        ) duplicates
    """), "legacy documents contain case-insensitive filename duplicates")
    cr.execute("""
        CREATE TABLE trucalc_pass4c2_context AS
        SELECT d.id, d.name, d.filename, d.order_id, d.company_id, d.uploaded_by,
               d.upload_date, a.id AS attachment_id, a.checksum, a.file_size, a.store_fname
        FROM trucalc_document d
        LEFT JOIN ir_attachment a ON a.res_model='trucalc.document'
             AND a.res_id=d.id AND a.res_field='attachment'
        ORDER BY d.id
    """)
    cr.execute("""
        CREATE TABLE IF NOT EXISTS trucalc_document_tag (
            id SERIAL PRIMARY KEY, name VARCHAR NOT NULL, active BOOLEAN NOT NULL DEFAULT TRUE,
            sequence INTEGER NOT NULL DEFAULT 10, create_uid INTEGER, write_uid INTEGER,
            create_date TIMESTAMP, write_date TIMESTAMP
        )
    """)
    cr.execute("CREATE UNIQUE INDEX IF NOT EXISTS trucalc_document_tag_name_ci ON trucalc_document_tag (lower(name))")
    tag_ids = {}
    for name, sequence, xmlid in (
        ('Prior Appraisal', 10, 'document_tag_prior_appraisal'),
        ('Other', 20, 'document_tag_other'),
    ):
        cr.execute("""
            INSERT INTO trucalc_document_tag (name, active, sequence, create_uid, write_uid, create_date, write_date)
            VALUES (%s, TRUE, %s, 1, 1, NOW(), NOW())
            ON CONFLICT ((lower(name))) DO UPDATE SET name=EXCLUDED.name
            RETURNING id
        """, (name, sequence))
        tag_id = cr.fetchone()[0]
        tag_ids[name] = tag_id
        cr.execute("""
            INSERT INTO ir_model_data (module, name, model, res_id, noupdate, create_uid, write_uid, create_date, write_date)
            VALUES ('trucalc_orders', %s, 'trucalc.document.tag', %s, TRUE, 1, 1, NOW(), NOW())
            ON CONFLICT (module, name) DO UPDATE SET model=EXCLUDED.model, res_id=EXCLUDED.res_id
        """, (xmlid, tag_id))
    cr.execute("ALTER TABLE trucalc_document ADD COLUMN IF NOT EXISTS tag_id INTEGER")
    cr.execute("ALTER TABLE trucalc_document ADD COLUMN IF NOT EXISTS origin VARCHAR")
    cr.execute("ALTER TABLE trucalc_document ADD COLUMN IF NOT EXISTS originating_bank_id INTEGER")
    cr.execute("ALTER TABLE trucalc_document ADD COLUMN IF NOT EXISTS visible_before_engagement BOOLEAN DEFAULT FALSE")
    cr.execute("ALTER TABLE trucalc_document ADD COLUMN IF NOT EXISTS visible_after_engagement BOOLEAN DEFAULT FALSE")
    cr.execute("ALTER TABLE trucalc_document ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE")
    cr.execute("ALTER TABLE trucalc_document ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP")
    cr.execute("ALTER TABLE trucalc_document ADD COLUMN IF NOT EXISTS deleted_by_id INTEGER")
    cr.execute("UPDATE trucalc_document SET tag_id=%s WHERE document_type='appraisal'", (tag_ids['Prior Appraisal'],))
    cr.execute("UPDATE trucalc_document SET tag_id=%s WHERE document_type='other'", (tag_ids['Other'],))
    cr.execute("""
        UPDATE trucalc_document SET origin='trucalc', originating_bank_id=NULL,
            visible_before_engagement=FALSE, visible_after_engagement=FALSE, active=TRUE
    """)
    _assert(_fetch(cr, "SELECT count(*)=0 FROM trucalc_document WHERE tag_id IS NULL"),
            "a legacy document could not be classified")
    cr.execute("ALTER TABLE trucalc_document ALTER COLUMN tag_id SET NOT NULL")
    cr.execute("ALTER TABLE trucalc_document ALTER COLUMN origin SET NOT NULL")
    cr.execute("ALTER TABLE trucalc_document ALTER COLUMN active SET NOT NULL")
    cr.execute("ALTER TABLE trucalc_document DROP COLUMN document_type")
    cr.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS trucalc_document_active_filename_unique
        ON trucalc_document (order_id, lower(filename)) WHERE active IS TRUE
    """)
