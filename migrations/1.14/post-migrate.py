def _fetch(cr, query, params=None):
    cr.execute(query, params or ())
    return cr.fetchone()[0]


def _assert(condition, message):
    if not condition:
        raise RuntimeError(f"Pass 4C.2 post-migration validation failed: {message}")


def migrate(cr, version):
    _assert(_fetch(cr, "SELECT count(*) FROM trucalc_document") == 3,
            "legacy document count changed")
    cr.execute("""
        SELECT d.filename, t.name, d.origin, d.visible_before_engagement,
               d.visible_after_engagement, d.active
        FROM trucalc_document d JOIN trucalc_document_tag t ON t.id=d.tag_id
        ORDER BY d.id
    """)
    _assert(cr.fetchall() == [
        ('Appraisal.pdf', 'Prior Appraisal', 'trucalc', False, False, True),
        ('Appendix H-1 UAD Compliance Rules-URAR 08012025.xlsx', 'Other', 'trucalc', False, False, True),
        ('Doc1.pdf', 'Other', 'trucalc', False, False, True),
    ], "legacy document classification or safe defaults are incorrect")
    _assert(_fetch(cr, """
        SELECT count(*)=0 FROM trucalc_pass4c2_context s
        LEFT JOIN trucalc_document d ON d.id=s.id
        LEFT JOIN ir_attachment a ON a.id=s.attachment_id
        WHERE d.id IS NULL OR d.name IS DISTINCT FROM s.name
           OR d.filename IS DISTINCT FROM s.filename OR d.order_id IS DISTINCT FROM s.order_id
           OR d.company_id IS DISTINCT FROM s.company_id OR d.uploaded_by IS DISTINCT FROM s.uploaded_by
           OR d.upload_date IS DISTINCT FROM s.upload_date OR a.id IS NULL
           OR a.checksum IS DISTINCT FROM s.checksum OR a.file_size IS DISTINCT FROM s.file_size
           OR a.store_fname IS DISTINCT FROM s.store_fname
    """), "legacy document metadata or binary attachments changed")
    _assert(_fetch(cr, """
        SELECT count(*)=0 FROM information_schema.columns
        WHERE table_name='trucalc_document' AND column_name='document_type'
    """), "legacy document_type was not retired")
    cr.execute("DROP TABLE trucalc_pass4c2_context")
