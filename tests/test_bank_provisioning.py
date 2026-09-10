from ast import literal_eval

from lxml import etree

from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "trucalc_bank_provisioning")
class TestBankProvisioning(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.main = cls.env.ref("base.main_company")
        cls.groups = {
            name: cls.env.ref("trucalc_orders.%s" % name)
            for name in (
                "group_trucalc_admin", "group_trucalc_operations",
                "group_trucalc_reviewer", "group_bank_admin",
                "group_bank_requestor", "group_bank_view_only",
                "group_vendor_portal",
            )
        }
        cls.admin = cls._user("admin", [cls.groups["group_trucalc_admin"]])
        cls.ops = cls._user("ops", [cls.groups["group_trucalc_operations"]])
        cls.reviewer = cls._user(
            "reviewer",
            [cls.groups["group_trucalc_operations"], cls.groups["group_trucalc_reviewer"]],
        )
        cls.internal = cls._user("ordinary-internal", [cls.env.ref("base.group_user")])
        cls.plain_portal = cls._user("plain-portal", [cls.env.ref("base.group_portal")])
        cls.vendor = cls.env["trucalc.vendor"].create({"name": "5A1 Vendor"})
        cls.vendor_user = cls._user(
            "vendor", [cls.groups["group_vendor_portal"]], vendor=cls.vendor
        )

    @classmethod
    def _user(cls, suffix, groups, bank=False, vendor=False, active=True):
        values = {
            "name": "5A1 %s" % suffix,
            "login": "5a1-%s@example.test" % suffix,
            "email": "5a1-%s@example.test" % suffix,
            "active": active,
            "group_ids": [Command.set([group.id for group in groups])],
            "trucalc_bank_company_id": bank.id if bank else False,
            "trucalc_vendor_id": vendor.id if vendor else False,
        }
        if bank:
            values.update({
                "company_id": bank.id,
                "company_ids": [Command.set(bank.ids)],
            })
        return cls.env["res.users"].with_context(no_reset_password=True).create(values)

    def _create_bank(self, suffix="Created", **extra):
        values = {
            "name": "5A1 %s Bank" % suffix,
            "currency_id": self.main.currency_id.id,
        }
        values.update(extra)
        return self.env["res.company"].with_user(self.admin)._trucalc_create_bank(values)

    def test_administrator_creates_exact_empty_bank_and_audit(self):
        before = {
            model: self.env[model].sudo().search_count([])
            for model in (
                "res.users", "trucalc.order", "trucalc.negotiated.fee",
                "trucalc.service.area",
            )
        }
        currency = self.env.ref("base.EUR")
        wizard = self.env["trucalc.bank.provision"].with_user(self.admin).create({
            "name": "5A1 Profile Bank",
            "currency_id": currency.id,
            "street": "10 Bank Way",
            "city": "Tupelo",
            "phone": "555-0100",
            "email": "bank@example.test",
            "website": "https://bank.example.test",
        })
        self.assertEqual(wizard.currency_id, currency)
        action = wizard.action_save()
        bank = self.env["res.company"].browse(action["res_id"])
        self.assertTrue(bank.trucalc_is_bank)
        self.assertTrue(bank.trucalc_bank_active)
        self.assertEqual(bank.parent_id, self.env["res.company"])
        self.assertEqual(bank.currency_id, currency)
        self.assertEqual(bank.street, "10 Bank Way")
        self.assertEqual(bank.city, "Tupelo")
        self.assertEqual(bank.email, "bank@example.test")
        self.assertEqual(
            self.env["trucalc.bank.admin.audit"].sudo().search([
                ("bank_company_id", "=", bank.id),
                ("event_type", "=", "bank_created"),
                ("actor_user_id", "=", self.admin.id),
            ]).mapped("new_status"),
            ["active"],
        )
        self.assertEqual(
            self.env["res.company"].sudo().with_context(active_test=False).search_count([
                ("name", "=", "5A1 Profile Bank"),
            ]),
            1,
        )
        self.assertEqual(
            {model: self.env[model].sudo().search_count([]) for model in before},
            {
                **before,
                "res.users": before["res.users"],
            },
        )
        self.assertIn(bank, self.admin.company_ids)
        self.assertIn(bank, self.ops.company_ids)
        self.assertIn(bank, self.reviewer.company_ids)
        for user in (self.internal, self.plain_portal, self.vendor_user):
            self.assertNotIn(bank, user.company_ids)

    def test_unauthorized_personas_cannot_create_or_use_wizard(self):
        bank = self._create_bank("Unauthorized Fixture")
        bank_user = self._user(
            "bank-user", [self.groups["group_bank_admin"]], bank=bank
        )
        actors = (
            self.ops, self.reviewer, self.internal, self.plain_portal,
            self.vendor_user, bank_user,
        )
        for index, actor in enumerate(actors):
            with self.assertRaises(AccessError):
                self.env["res.company"].with_user(actor)._trucalc_create_bank({
                    "name": "5A1 Denied %s" % index,
                    "currency_id": self.main.currency_id.id,
                })
            with self.assertRaises(AccessError):
                self.env["trucalc.bank.provision"].with_user(actor).create({
                    "name": "5A1 Denied Wizard %s" % index,
                    "currency_id": self.main.currency_id.id,
                })

    def test_name_currency_main_company_and_raw_status_validation(self):
        for name in (False, "", "   \n  "):
            with self.assertRaises(ValidationError):
                self.env["res.company"].with_user(self.admin)._trucalc_create_bank({
                    "name": name,
                    "currency_id": self.main.currency_id.id,
                })
        bank = self._create_bank("Normalized   Name")
        self.assertEqual(bank.name, "5A1 Normalized Name Bank")
        with self.assertRaises(ValidationError):
            self.env["res.company"].with_user(self.admin)._trucalc_create_bank({
                "name": "  5a1 normalized name bank  ",
                "currency_id": self.main.currency_id.id,
            })
        with self.assertRaises(ValidationError):
            self.env["res.company"].with_user(self.admin)._trucalc_create_bank({
                "name": "5A1 No Currency",
                "currency_id": False,
            })
        with self.assertRaises(AccessError):
            self.main.write({"trucalc_is_bank": True})
        with self.assertRaises(AccessError):
            bank.write({"trucalc_bank_active": False})
        with self.assertRaises(AccessError):
            bank.unlink()

    def test_controlled_profile_update(self):
        bank = self._create_bank("Editable")
        edit_action = bank.with_user(self.admin).action_trucalc_edit_bank()
        self.assertEqual(
            edit_action["context"]["default_bank_company_id"], bank.id
        )
        Wizard = self.env["trucalc.bank.provision"].with_user(
            self.admin
        ).with_context(**edit_action["context"])
        wizard = Wizard.create({})
        self.assertEqual(wizard.bank_company_id, bank)
        self.assertEqual(wizard.name, bank.name)
        self.assertEqual(wizard.currency_id, bank.currency_id)

        currency = self.env.ref("base.EUR")
        state = self.env.ref("base.state_us_28")
        logo = (
            b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lE"
            b"QVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        wizard.write({
            "name": "  5A1 Edited Bank  ",
            "currency_id": currency.id,
            "street": "20 Edited Street",
            "street2": "Suite 5A1",
            "city": "Pontotoc",
            "state_id": state.id,
            "zip": "38863",
            "country_id": state.country_id.id,
            "phone": "555-0200",
            "email": "edited@example.test",
            "website": "https://edited.example.test",
            "logo": logo,
        })
        result = wizard.action_save()
        self.assertEqual(result["res_id"], bank.id)
        bank.invalidate_recordset()
        self.assertEqual(bank.name, "5A1 Edited Bank")
        self.assertEqual(bank.currency_id, currency)
        self.assertEqual(bank.street, "20 Edited Street")
        self.assertEqual(bank.street2, "Suite 5A1")
        self.assertEqual(bank.city, "Pontotoc")
        self.assertEqual(bank.state_id, state)
        self.assertEqual(bank.zip, "38863")
        self.assertEqual(bank.country_id, state.country_id)
        self.assertEqual(bank.phone, "555-0200")
        self.assertEqual(bank.website, "https://edited.example.test")
        self.assertEqual(bank.logo, logo)

        duplicate = self._create_bank("Duplicate Edit")
        for rejected_name in ("   ", duplicate.name):
            wizard.write({"name": rejected_name})
            with self.assertRaises(ValidationError):
                wizard.action_save()
        bank.invalidate_recordset()
        self.assertEqual(bank.name, "5A1 Edited Bank")

        with self.assertRaises(AccessError):
            bank.with_user(self.ops)._trucalc_update_bank_profile({
                "name": bank.name, "currency_id": bank.currency_id.id,
            })
        with self.assertRaises(AccessError):
            bank.with_user(self.ops).action_trucalc_edit_bank()
        unmarked = self.env["res.company"].create({"name": "5A1 Unmarked Company"})
        with self.assertRaises(AccessError):
            unmarked.with_user(self.admin).action_trucalc_edit_bank()

    def test_administration_surface_is_narrow_and_administrator_only(self):
        action = self.env.ref("trucalc_orders.action_trucalc_banks")
        menu = self.env.ref("trucalc_orders.menu_trucalc_banks")
        self.assertEqual(action.group_ids, self.groups["group_trucalc_admin"])
        self.assertEqual(menu.group_ids, self.groups["group_trucalc_admin"])
        self.assertEqual(literal_eval(action.domain), [("trucalc_is_bank", "=", True)])

        form = etree.fromstring(
            self.env.ref("trucalc_orders.view_trucalc_bank_form").arch
        )
        self.assertEqual(
            (form.get("create"), form.get("edit"), form.get("delete")),
            ("false", "false", "false"),
        )
        deactivate = form.xpath(
            ".//button[@name='action_trucalc_deactivate_bank']"
        )
        self.assertEqual(len(deactivate), 1)
        self.assertTrue(deactivate[0].get("confirm"))
        exposed_fields = set(form.xpath(".//field/@name"))
        self.assertFalse(exposed_fields & {
            "company_ids", "group_ids", "password", "trucalc_vendor_id",
            "trucalc_bank_company_id",
        })

        audit = self.env["trucalc.bank.admin.audit"]
        self.assertTrue(audit.with_user(self.admin).has_access("read"))
        for actor in (
            self.ops, self.reviewer, self.internal, self.plain_portal,
            self.vendor_user,
        ):
            self.assertFalse(audit.with_user(actor).has_access("read"))

        with self.assertRaises(AccessError):
            self.main.with_user(self.admin).action_trucalc_deactivate_bank()

    def test_deactivation_archives_users_preserves_history_and_reactivation_is_explicit(self):
        bank = self._create_bank("Lifecycle")
        active_user = self._user(
            "active-bank", [self.groups["group_bank_requestor"]], bank=bank
        )
        inactive_user = self._user(
            "inactive-bank", [self.groups["group_bank_view_only"]],
            bank=bank, active=False,
        )
        protected_counts = {
            model: self.env[model].sudo().search_count([])
            for model in (
                "trucalc.order", "trucalc.bank.invoice",
                "trucalc.negotiated.fee", "trucalc.document",
                "trucalc.order.lifecycle.event",
            )
        }
        bank.with_user(self.admin).action_trucalc_deactivate_bank()
        bank.invalidate_recordset()
        active_user.invalidate_recordset()
        inactive_user.invalidate_recordset()
        self.assertFalse(bank.trucalc_bank_active)
        self.assertFalse(active_user.active)
        self.assertFalse(inactive_user.active)
        with self.assertRaises(AccessError):
            active_user._trucalc_bank_identity()
        self.assertEqual(
            {model: self.env[model].sudo().search_count([]) for model in protected_counts},
            protected_counts,
        )
        events = self.env["trucalc.bank.admin.audit"].sudo().search([
            ("bank_company_id", "=", bank.id),
        ])
        self.assertEqual(
            len(events.filtered(lambda event: event.event_type == "bank_deactivated")),
            1,
        )
        user_events = events.filtered(
            lambda event: event.event_type == "bank_user_deactivated_by_bank_deactivation"
        )
        self.assertEqual(user_events.target_user_id, active_user)

        bank.with_user(self.admin).action_trucalc_activate_bank()
        bank.invalidate_recordset()
        active_user.invalidate_recordset()
        self.assertTrue(bank.trucalc_bank_active)
        self.assertFalse(active_user.active)
        self.assertEqual(
            self.env["trucalc.bank.admin.audit"].sudo().search_count([
                ("bank_company_id", "=", bank.id),
                ("event_type", "=", "bank_activated"),
            ]),
            1,
        )

    def test_bank_identity_requires_marker_status_and_exact_company_binding(self):
        bank = self._create_bank("Identity")
        user = self._user(
            "identity", [self.groups["group_bank_admin"]], bank=bank
        )
        self.assertEqual(user._trucalc_bank_identity(), bank)

        bank.with_user(self.admin).action_trucalc_deactivate_bank()
        user.sudo().write({"active": True})
        with self.assertRaises(AccessError):
            user._trucalc_bank_identity()
        with self.assertRaises(AccessError):
            self.env["trucalc.order"].with_user(user).create({
                "borrower": "Blocked",
                "property_address": "1 Inactive Bank Way",
            })

    def test_audit_is_append_only(self):
        bank = self._create_bank("Audit")
        event = self.env["trucalc.bank.admin.audit"].sudo().search([
            ("bank_company_id", "=", bank.id),
        ], limit=1)
        with self.assertRaises(AccessError):
            self.env["trucalc.bank.admin.audit"].with_user(self.admin).create({
                "event_type": "bank_created",
                "event_at": event.event_at,
                "actor_user_id": self.admin.id,
                "bank_company_id": bank.id,
            })
        with self.assertRaises(AccessError):
            event.with_user(self.admin).write({"new_status": "changed"})
        with self.assertRaises(AccessError):
            event.with_user(self.admin).unlink()


@tagged("post_install", "-at_install", "trucalc_bank_migration")
class TestBankMigrationResults(TransactionCase):
    def test_authoritative_existing_bank_migration_results_when_present(self):
        names = ("Cadence Bank", "Renassant Bank", "First Choice Bank")
        banks = self.env["res.company"].sudo().with_context(active_test=False).search([
            ("name", "in", names),
        ])
        if not banks:
            self.skipTest("Authoritative live Bank fixtures are not present on a fresh install")
        self.assertEqual(set(banks.mapped("name")), set(names))
        self.assertTrue(all(banks.mapped("trucalc_is_bank")))
        self.assertTrue(all(banks.mapped("trucalc_bank_active")))
        self.assertFalse(self.env.ref("base.main_company").trucalc_is_bank)
        for user in self.env["res.users"].sudo().with_context(active_test=False).search([
            ("trucalc_bank_company_id", "!=", False),
        ]):
            self.assertEqual(user.company_id, user.trucalc_bank_company_id)
            self.assertEqual(user.company_ids, user.trucalc_bank_company_id)
