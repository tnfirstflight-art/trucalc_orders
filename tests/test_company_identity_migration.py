import importlib.util
from pathlib import Path

from odoo.tests import TransactionCase, tagged


ROOT = Path(__file__).resolve().parents[1]


def _load_migration(stage):
    path = ROOT / "migrations" / "1.31" / (stage + "-migrate.py")
    spec = importlib.util.spec_from_file_location(
        "trucalc_company_identity_%s" % stage.replace("-", "_"), path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PRE = _load_migration("pre")
POST = _load_migration("post")


@tagged(
    "post_install",
    "-at_install",
    "trucalc_company_identity_migration",
)
class TestCompanyIdentityMigration(TransactionCase):
    def _set_company_name(self, name):
        self.env.cr.execute(
            "UPDATE res_company SET name = %s WHERE id = %s",
            (name, self.env.ref("base.main_company").id),
        )
        self.env.invalidate_all()

    def _identity(self):
        self.env.cr.execute(
            """
            SELECT data.id, data.res_id, company.name
              FROM ir_model_data data
              JOIN res_company company ON company.id = data.res_id
             WHERE data.module = 'base'
               AND data.name = 'main_company'
               AND data.model = 'res.company'
            """
        )
        return self.env.cr.fetchone()

    def _invoice_history(self):
        self.env.cr.execute(
            """
            SELECT count(*), md5(COALESCE(string_agg(
                concat_ws('|', id, printed_values::text, encode(pdf_data, 'hex')),
                E'\n' ORDER BY id
            ), ''))
              FROM trucalc_bank_invoice
            """
        )
        return self.env.cr.fetchone()

    def _company_links(self):
        self.env.cr.execute("SELECT cid, user_id FROM res_company_users_rel")
        return set(self.env.cr.fetchall())

    def test_old_name_is_changed_without_reidentifying_company_or_history(self):
        self._set_company_name(PRE.SOURCE_NAME)
        identity_before = self._identity()
        links_before = self._company_links()
        invoice_history_before = self._invoice_history()

        PRE.migrate(self.env.cr, "1.30")
        POST.migrate(self.env.cr, "1.30")
        self.env.invalidate_all()

        identity_after = self._identity()
        self.assertEqual(identity_after[:2], identity_before[:2])
        self.assertEqual(identity_after[2], PRE.TARGET_NAME)
        self.assertEqual(self._company_links(), links_before)
        self.assertEqual(self._invoice_history(), invoice_history_before)
        self.assertEqual(self.env.ref("base.main_company").name, PRE.TARGET_NAME)

    def test_already_migrated_name_is_noop(self):
        self._set_company_name(PRE.TARGET_NAME)
        identity_before = self._identity()
        PRE.migrate(self.env.cr, "1.30")
        POST.migrate(self.env.cr, "1.30")
        self.env.invalidate_all()
        self.assertEqual(self._identity(), identity_before)

    def test_unexpected_name_blocks_without_mutation(self):
        unexpected = "Unexpected Main Company Identity"
        self._set_company_name(unexpected)
        identity_before = self._identity()
        with self.assertRaisesRegex(RuntimeError, "unexpected name"):
            PRE.migrate(self.env.cr, "1.30")
        self.assertEqual(self._identity(), identity_before)
        self.env.cr.execute("SELECT to_regclass(%s)", (PRE.CONTEXT_TABLE,))
        self.assertFalse(self.env.cr.fetchone()[0])
