import json


SOURCE_NAME = "TruCalc Valuation Services, LLC"
TARGET_NAME = "TruCalc Valuation Solutions"
CONTEXT_TABLE = "trucalc_1_31_company_identity_context"


PROTECTED_QUERIES = {
    "main_company_except_name": """
        SELECT count(*), md5(COALESCE(string_agg(
            (to_jsonb(record) - 'name')::text, E'\n' ORDER BY id
        ), ''))
          FROM res_company record
         WHERE id = %s
    """,
    "other_companies": """
        SELECT count(*), md5(COALESCE(string_agg(
            to_jsonb(record)::text, E'\n' ORDER BY id
        ), ''))
          FROM res_company record
         WHERE id <> %s
    """,
    "company_user_links": """
        SELECT count(*), md5(COALESCE(string_agg(
            concat_ws('|', cid, user_id), E'\n' ORDER BY cid, user_id
        ), ''))
          FROM res_company_users_rel
    """,
    "orders": """
        SELECT count(*), md5(COALESCE(string_agg(
            to_jsonb(record)::text, E'\n' ORDER BY id
        ), ''))
          FROM trucalc_order record
    """,
    "invoices": """
        SELECT count(*), md5(COALESCE(string_agg(
            to_jsonb(record)::text, E'\n' ORDER BY id
        ), ''))
          FROM trucalc_bank_invoice record
    """,
    "documents": """
        SELECT count(*), md5(COALESCE(string_agg(
            to_jsonb(record)::text, E'\n' ORDER BY id
        ), ''))
          FROM trucalc_document record
    """,
    "vendor_deliverables": """
        SELECT count(*), md5(COALESCE(string_agg(
            to_jsonb(record)::text, E'\n' ORDER BY id
        ), ''))
          FROM trucalc_vendor_deliverable record
    """,
    "lifecycle_events": """
        SELECT count(*), md5(COALESCE(string_agg(
            to_jsonb(record)::text, E'\n' ORDER BY id
        ), ''))
          FROM trucalc_order_lifecycle_event record
    """,
    "mail_messages": """
        SELECT count(*), md5(COALESCE(string_agg(
            to_jsonb(record)::text, E'\n' ORDER BY id
        ), ''))
          FROM mail_message record
    """,
    "mail_queue": """
        SELECT count(*), md5(COALESCE(string_agg(
            to_jsonb(record)::text, E'\n' ORDER BY id
        ), ''))
          FROM mail_mail record
    """,
    "bank_admin_audit": """
        SELECT count(*), md5(COALESCE(string_agg(
            to_jsonb(record)::text, E'\n' ORDER BY id
        ), ''))
          FROM trucalc_bank_admin_audit record
    """,
    "internal_user_admin_audit": """
        SELECT count(*), md5(COALESCE(string_agg(
            to_jsonb(record)::text, E'\n' ORDER BY id
        ), ''))
          FROM trucalc_internal_user_admin_audit record
    """,
}


def _assert(condition, message):
    if not condition:
        raise RuntimeError(
            "TruCalc 1.31 company identity post-migration failed: %s" % message
        )


def _main_company(cr):
    cr.execute(
        """
        SELECT data.id, data.res_id, company.name
          FROM ir_model_data data
          JOIN res_company company ON company.id = data.res_id
         WHERE data.module = 'base'
           AND data.name = 'main_company'
           AND data.model = 'res.company'
        """
    )
    rows = cr.fetchall()
    _assert(len(rows) == 1, "base.main_company is missing or ambiguous")
    return rows[0]


def _protected_state(cr, company_id):
    state = {}
    for key, query in PROTECTED_QUERIES.items():
        params = (company_id,) if "%s" in query else ()
        cr.execute(query, params)
        state[key] = list(cr.fetchone())
    return state


def migrate(cr, version):
    cr.execute(
        """
        SELECT external_id, company_id, source_name, protected_state
          FROM trucalc_1_31_company_identity_context
        """
    )
    rows = cr.fetchall()
    _assert(len(rows) == 1, "migration context is missing or ambiguous")
    external_id, company_id, source_name, protected_state = rows[0]

    current_external_id, current_company_id, current_name = _main_company(cr)
    _assert(current_external_id == external_id, "base.main_company external ID changed")
    _assert(current_company_id == company_id, "base.main_company record ID changed")
    _assert(current_name == source_name, "base.main_company changed during module loading")

    if source_name == SOURCE_NAME:
        cr.execute(
            """
            UPDATE res_company
               SET name = %s
             WHERE id = %s
               AND name = %s
            """,
            (TARGET_NAME, company_id, SOURCE_NAME),
        )
        _assert(cr.rowcount == 1, "expected old-name company was not updated exactly once")
    else:
        _assert(source_name == TARGET_NAME, "unexpected migration source state")

    _assert(_main_company(cr)[2] == TARGET_NAME, "target company name was not applied")
    expected_state = protected_state
    if isinstance(expected_state, str):
        expected_state = json.loads(expected_state)
    actual_state = _protected_state(cr, company_id)
    _assert(actual_state == expected_state, "protected business or historical data changed")

    cr.execute("DROP TABLE trucalc_1_31_company_identity_context")
