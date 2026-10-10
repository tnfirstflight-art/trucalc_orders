from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from lxml import etree

from odoo import Command, fields
from odoo.addons.mail.models.mail_mail import MailMail
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "trucalc_bank_user_provisioning")
class TestBankUserProvisioning(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.main = cls.env.ref("base.main_company")
        cls.group_admin = cls.env.ref("trucalc_orders.group_trucalc_admin")
        cls.group_ops = cls.env.ref("trucalc_orders.group_trucalc_operations")
        cls.group_reviewer = cls.env.ref("trucalc_orders.group_trucalc_reviewer")
        cls.group_vendor = cls.env.ref("trucalc_orders.group_vendor_portal")
        cls.roles = {
            "administrator": cls.env.ref("trucalc_orders.group_bank_admin"),
            "requestor": cls.env.ref("trucalc_orders.group_bank_requestor"),
            "view_only": cls.env.ref("trucalc_orders.group_bank_view_only"),
        }
        cls.admin = cls._user("admin", [cls.group_admin])
        cls.ops = cls._user("ops", [cls.group_ops])
        cls.reviewer = cls._user("reviewer", [cls.group_ops, cls.group_reviewer])
        cls.internal = cls._user("internal", [cls.env.ref("base.group_user")])
        cls.portal = cls._user("portal", [cls.env.ref("base.group_portal")])
        cls.vendor = cls.env["trucalc.vendor"].create({"name": "5A2 Vendor"})
        cls.vendor_user = cls._user("vendor", [cls.group_vendor], vendor=cls.vendor)
        Companies = cls.env["res.company"].with_context(trucalc_test_bank_fixture=True)
        cls.bank = Companies.create({
            "name": "5A2 Bank", "currency_id": cls.main.currency_id.id,
            "trucalc_is_bank": True, "trucalc_bank_active": True,
        })
        cls.other_bank = Companies.create({
            "name": "5A2 Other Bank", "currency_id": cls.main.currency_id.id,
            "trucalc_is_bank": True, "trucalc_bank_active": True,
        })

    @classmethod
    def _user(cls, suffix, groups, bank=False, vendor=False, active=True, login=False):
        login = login or "5a2-%s@example.test" % suffix
        values = {
            "name": "5A2 %s" % suffix, "login": login, "email": login,
            "active": active,
            "group_ids": [Command.set([group.id for group in groups])],
            "trucalc_bank_company_id": bank.id if bank else False,
            "trucalc_vendor_id": vendor.id if vendor else False,
        }
        if bank:
            values.update({
                "company_id": bank.id,
                "company_ids": [Command.set([bank.id])],
            })
        return cls.env["res.users"].with_context(no_reset_password=True).create(values)

    def _wizard(
        self, suffix, role="requestor", bank=False, active=True, actor=False,
        phone=False,
    ):
        actor = actor or self.admin
        return self.env["trucalc.bank.user.provision"].with_user(actor).create({
            "bank_company_id": (bank or self.bank).id,
            "name": "5A2 Provisioned %s" % suffix,
            "login": "5a2-provisioned-%s@example.test" % suffix,
            "phone": phone, "role": role, "active": active,
        })

    def _assert_exact(self, user, bank, role, active=True):
        user = user.sudo().with_context(active_test=False)
        self.assertEqual(user.active, active)
        self.assertTrue(user.share)
        self.assertEqual(user._trucalc_bank_role_key(), role)
        self.assertEqual(user._trucalc_persona_membership()["bank"], self.roles[role])
        self.assertIn(self.env.ref("base.group_portal"), user.all_group_ids)
        self.assertNotIn(self.env.ref("base.group_user"), user.all_group_ids)
        self.assertNotIn(self.env.ref("base.group_public"), user.all_group_ids)
        self.assertFalse(user.trucalc_vendor_id)
        self.assertEqual(user.trucalc_bank_company_id, bank)
        self.assertEqual(user.company_id, bank)
        self.assertEqual(user.company_ids, bank)

    def _email_change_wizard(
        self, target, new_login, bank=False, actor=False, acknowledge=False,
    ):
        bank = bank or self.bank
        actor = actor or self.admin
        context = {
            "default_bank_company_id": bank.id,
            "default_target_user_id": target.id,
            "trucalc_locked_bank_company_id": bank.id,
            "trucalc_locked_target_user_id": target.id,
        }
        return self.env["trucalc.bank.user.email.change"].with_user(
            actor
        ).with_context(**context).create({
            "new_login": new_login,
            "acknowledge_invitation_invalidation": acknowledge,
        })

    def _manage_wizard(self, target, bank=False, actor=False):
        bank = bank or self.bank
        actor = actor or self.admin
        action = bank.with_user(actor).action_trucalc_view_bank_users()
        row = self.env["trucalc.bank.user.management"].with_user(actor).search(
            action["domain"]
        ).filtered(lambda item: item.target_user_id == target)
        self.assertEqual(len(row), 1)
        manage_action = row.action_manage()
        self.assertEqual(manage_action["res_model"], "trucalc.bank.user.manage")
        self.assertEqual(manage_action["target"], "new")
        return self.env["trucalc.bank.user.manage"].with_user(actor).with_context(
            **manage_action["context"]
        ).browse(manage_action["res_id"])

    def test_wizard_provisions_all_roles_without_invitation_or_password(self):
        for role in self.roles:
            suffix = role.replace("_", "-")
            wizard = self._wizard(suffix, role)
            before = self.env["res.users"].sudo().with_context(active_test=False).search_count([])
            wizard.action_save()
            user = self.env["res.users"].sudo().search([
                ("login", "=", "5a2-provisioned-%s@example.test" % suffix),
            ])
            self.assertEqual(
                self.env["res.users"].sudo().with_context(active_test=False).search_count([]),
                before + 1,
            )
            self._assert_exact(user, self.bank, role)
            self.assertFalse(user.partner_id.signup_type)
            self.assertEqual(user.state, "new")
            event = self.env["trucalc.bank.admin.audit"].sudo().search([
                ("event_type", "=", "bank_user_created"),
                ("target_user_id", "=", user.id),
            ])
            self.assertEqual(event.actor_user_id, self.admin)
            self.assertEqual(event.metadata["role"], role)
        arch = etree.fromstring(
            self.env.ref("trucalc_orders.view_trucalc_bank_user_provision_form").arch
        )
        self.assertFalse(set(arch.xpath(".//field/@name")) & {
            "password", "group_ids", "company_id", "company_ids",
            "trucalc_bank_company_id", "trucalc_vendor_id",
        })

    def test_real_bank_form_add_user_action_prefills_fixed_bank_and_saves(self):
        form = etree.fromstring(
            self.env.ref("trucalc_orders.view_trucalc_bank_form").arch
        )
        buttons = form.xpath(
            ".//button[@name='action_trucalc_add_bank_user']"
        )
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0].get("string"), "Add Bank User")
        self.assertEqual(buttons[0].get("invisible"), "not trucalc_bank_active")
        self.assertEqual(
            buttons[0].get("groups"), "trucalc_orders.group_trucalc_admin"
        )

        action = self.bank.with_user(self.admin).action_trucalc_add_bank_user()
        self.assertEqual(action["res_model"], "trucalc.bank.user.provision")
        self.assertEqual(action["target"], "new")
        self.assertEqual(action["context"]["default_bank_company_id"], self.bank.id)
        self.assertEqual(
            action["context"]["trucalc_locked_bank_company_id"], self.bank.id
        )
        Wizard = self.env["trucalc.bank.user.provision"].with_user(
            self.admin
        ).with_context(**action["context"])
        defaults = Wizard.default_get(["bank_company_id", "role", "active"])
        self.assertEqual(defaults["bank_company_id"], self.bank.id)
        wizard = Wizard.create({
            "name": "5A2 Direct Bank Requestor",
            "login": "5a2-direct-requestor@example.test",
            "role": "requestor",
            "active": True,
        })
        self.assertEqual(wizard.bank_company_id, self.bank)
        wizard.action_save()
        user = self.env["res.users"].sudo().search([
            ("login", "=", "5a2-direct-requestor@example.test"),
        ])
        self._assert_exact(user, self.bank, "requestor")
        self.assertFalse(user.partner_id.signup_type)
        self.assertEqual(
            self.env["trucalc.bank.admin.audit"].sudo().search_count([
                ("event_type", "=", "bank_user_created"),
                ("target_user_id", "=", user.id),
            ]),
            1,
        )

        with self.assertRaises(AccessError):
            Wizard.create({
                "bank_company_id": self.other_bank.id,
                "name": "5A2 Switched Bank",
                "login": "5a2-switched-bank@example.test",
                "role": "requestor",
            })

    def test_creation_phone_and_fresh_post_create_projection(self):
        stale_action = self.bank.with_user(
            self.admin
        ).action_trucalc_view_bank_users()
        stale_rows = self.env["trucalc.bank.user.management"].with_user(
            self.admin
        ).search(stale_action["domain"])

        wizard = self._wizard("phone-refresh", phone="  +1 (615) 555-0134 ext. 9  ")
        fresh_action = wizard.action_save()
        target = self.env["res.users"].sudo().search([
            ("login", "=", "5a2-provisioned-phone-refresh@example.test"),
        ])
        self.assertEqual(target.phone, "(615) 555-0134 ext. 9")
        self.assertEqual(target.partner_id.phone, target.phone)
        self.assertNotIn(target, stale_rows.target_user_id)
        fresh_rows = self.env["trucalc.bank.user.management"].with_user(
            self.admin
        ).search(fresh_action["domain"])
        self.assertIn(target, fresh_rows.target_user_id)
        self.assertEqual(fresh_action["res_model"], "trucalc.bank.user.management")
        self.assertEqual(fresh_action["context"]["active_bank_company_id"], self.bank.id)

        blank = self._wizard("blank-phone")
        blank.action_save()
        blank_target = self.env["res.users"].sudo().search([
            ("login", "=", "5a2-provisioned-blank-phone@example.test"),
        ])
        self.assertFalse(blank_target.phone)

    def test_unified_manage_atomic_save_noop_and_history_preservation(self):
        target = self._user(
            "unified-save", [self.roles["requestor"]], bank=self.bank,
        )
        target.phone = "6155550100"
        target.with_context(no_reset_password=True).write({
            "password": "Unified-A2-Existing-Password",
        })
        target.partner_id.sudo().signup_prepare(signup_type="signup:unified-a2")
        order = self.env["trucalc.order"].sudo().create({
            "borrower": "Unified A2 History",
            "property_address": "22 Modal Way",
            "due_date": fields.Date.add(fields.Date.today(), days=7),
            "company_id": self.bank.id,
            "requestor_company_id": self.bank.id,
            "requestor_id": target.id,
        })
        self.env.cr.execute("SELECT password FROM res_users WHERE id=%s", [target.id])
        password_hash = self.env.cr.fetchone()[0]
        user_id, partner_id, order_id = target.id, target.partner_id.id, order.id
        mail_ids = self.env["mail.mail"].sudo().search([]).ids

        wizard = self._manage_wizard(target)
        self.assertEqual(wizard.phone, "(615) 555-0100")
        self.assertEqual(wizard.account_state, "invitation_pending")
        wizard.write({
            "name": "  Unified   Bank User  ",
            "login": "Unified.A2@Example.TEST",
            "phone": "+1 (615) 555-0198 extension 44",
            "role": "administrator",
            "acknowledge_account_link_invalidation": True,
        })
        fresh_action = wizard.action_save()
        target.invalidate_recordset()
        target.partner_id.invalidate_recordset()
        order.invalidate_recordset()
        self.assertEqual((target.id, target.partner_id.id, order.id), (
            user_id, partner_id, order_id,
        ))
        self.assertEqual(target.name, "Unified Bank User")
        self.assertEqual(target.login, "unified.a2@example.test")
        self.assertEqual(target.email, target.login)
        self.assertEqual(target.phone, "(615) 555-0198 ext. 44")
        self.assertEqual(target._trucalc_bank_role_key(), "administrator")
        self.assertFalse(target.partner_id.signup_type)
        self.assertEqual(order.requestor_id, target)
        self._assert_exact(target, self.bank, "administrator")
        self.env.cr.execute("SELECT password FROM res_users WHERE id=%s", [target.id])
        self.assertEqual(self.env.cr.fetchone()[0], password_hash)
        new_mails = self.env["mail.mail"].sudo().search([
            ("id", "not in", mail_ids),
        ])
        self.assertFalse(new_mails, new_mails.mapped(lambda mail: (
            mail.subject, mail.email_to, mail.state,
            mail.mail_message_id.model, mail.mail_message_id.res_id,
        )))
        fresh_rows = self.env["trucalc.bank.user.management"].with_user(
            self.admin
        ).search(fresh_action["domain"])
        self.assertIn(target, fresh_rows.target_user_id)
        events = self.env["trucalc.bank.admin.audit"].sudo().search([
            ("target_user_id", "=", target.id),
        ]).mapped("event_type")
        self.assertIn("bank_user_identity_updated", events)
        self.assertIn("bank_user_email_updated", events)
        self.assertIn("role_changed", events)

        noop = self._manage_wizard(target)
        audit_count = self.env["trucalc.bank.admin.audit"].sudo().search_count([
            ("target_user_id", "=", target.id),
        ])
        noop.action_save()
        self.assertEqual(self.env["trucalc.bank.admin.audit"].sudo().search_count([
            ("target_user_id", "=", target.id),
        ]), audit_count)

    def test_unified_manage_rolls_back_and_rejects_forgery(self):
        target = self._user(
            "unified-rollback", [self.roles["requestor"]], bank=self.bank,
        )
        collision = self._user(
            "unified-collision", [self.roles["view_only"]], bank=self.bank,
        )
        wizard = self._manage_wizard(target)
        original = (target.name, target.login, target.phone, target._trucalc_bank_role_key())
        wizard.write({
            "name": "Must Roll Back", "login": collision.login,
            "phone": "615-555-0111", "role": "administrator",
        })
        with self.assertRaises(ValidationError):
            wizard.action_save()
        target.invalidate_recordset()
        self.assertEqual(
            (target.name, target.login, target.phone, target._trucalc_bank_role_key()),
            original,
        )
        for values in (
            {"owner_user_id": self.ops.id},
            {"management_row_id": False},
            {"bank_company_id": self.other_bank.id},
            {"target_user_id": collision.id},
            {"user_active": False},
        ):
            with self.assertRaises(AccessError):
                wizard.write(values)
        for actor in (
            self.ops, self.reviewer, self.internal, self.portal, self.vendor_user,
        ):
            with self.assertRaises(AccessError):
                wizard.with_user(actor).action_save()

    def test_unified_manage_account_actions_guard_unsaved_edits_and_are_explicit(self):
        self.main.email = "trucalc@example.test"
        target = self._user(
            "unified-actions", [self.roles["requestor"]], bank=self.bank,
        )
        wizard = self._manage_wizard(target)
        wizard.name = "Unsaved Name"
        with self.assertRaisesRegex(ValidationError, "Save or discard"):
            wizard.action_send_invitation()
        self.assertFalse(target.partner_id.signup_type)

        wizard.name = target.name
        mail_count = self.env["mail.mail"].sudo().search_count([])

        def queued(mails, auto_commit=False, raise_exception=False,
                   post_send_callback=None):
            return True

        with patch.object(MailMail, "send", autospec=True, side_effect=queued):
            result = wizard.action_send_invitation()
        self.assertEqual(result["params"]["type"], "warning")
        self.assertIn("queued", result["params"]["message"].casefold())
        self.assertEqual(self.env["mail.mail"].sudo().search_count([]), mail_count + 1)
        target.invalidate_recordset()
        pending = self._manage_wizard(target)
        self.assertEqual(pending.account_state, "invitation_pending")
        with self.assertRaises(ValidationError):
            pending.action_send_invitation()

        before_deactivate_mail = self.env["mail.mail"].sudo().search_count([])
        pending.action_deactivate()
        target.invalidate_recordset()
        self.assertFalse(target.active)
        self.assertEqual(
            self.env["mail.mail"].sudo().search_count([]), before_deactivate_mail,
        )
        inactive = self._manage_wizard(target)
        self.assertEqual(inactive.account_state, "inactive")
        inactive.action_reactivate()
        target.invalidate_recordset()
        self.assertTrue(target.active)
        self.assertEqual(
            self.env["mail.mail"].sudo().search_count([]), before_deactivate_mail,
        )

    def test_controlled_edit_name_phone_audit_and_immutable_fields(self):
        target = self._user(
            "identity-edit", [self.roles["requestor"]], bank=self.bank,
        )
        target.phone = "615-555-0100"
        invariant = {
            "login": target.login,
            "email": target.email,
            "role": target._trucalc_bank_role_key(),
            "active": target.active,
            "share": target.share,
            "bank": target.trucalc_bank_company_id,
            "company": target.company_id,
            "companies": target.company_ids,
            "groups": target.group_ids,
            "vendor": target.trucalc_vendor_id,
            "signup_type": target.partner_id.signup_type,
        }
        management_action = self.bank.with_user(
            self.admin
        ).action_trucalc_view_bank_users()
        row = self.env["trucalc.bank.user.management"].with_user(
            self.admin
        ).search(management_action["domain"]).filtered(
            lambda item: item.target_user_id == target
        )
        self.assertEqual(row.phone, "615-555-0100")
        edit_action = row.action_edit()
        self.assertEqual(edit_action["res_model"], "trucalc.bank.user.edit")
        self.assertEqual(edit_action["target"], "new")
        Edit = self.env["trucalc.bank.user.edit"].with_user(
            self.admin
        ).with_context(**edit_action["context"])
        defaults = Edit.default_get([
            "owner_user_id", "bank_company_id", "target_user_id", "login",
            "name", "phone",
        ])
        self.assertEqual(defaults["owner_user_id"], self.admin.id)
        self.assertEqual(defaults["bank_company_id"], self.bank.id)
        self.assertEqual(defaults["target_user_id"], target.id)
        self.assertEqual(defaults["login"], target.login)
        wizard = Edit.create({
            "bank_company_id": self.bank.id,
            "target_user_id": target.id,
            "login": "attempted-change@example.test",
            "name": "  Updated   Bank User  ",
            "phone": "  +44 20 7946 0958 x42  ",
        })
        fresh_action = wizard.action_save()
        target.invalidate_recordset()
        self.assertEqual(target.name, "Updated Bank User")
        self.assertEqual(target.phone, "+44 20 7946 0958 x42")
        self.assertEqual(target.partner_id.phone, target.phone)
        fresh_rows = self.env["trucalc.bank.user.management"].with_user(
            self.admin
        ).search(fresh_action["domain"])
        self.assertIn(target, fresh_rows.target_user_id)
        for field_name, value in invariant.items():
            self.assertEqual(
                {
                    "login": target.login,
                    "email": target.email,
                    "role": target._trucalc_bank_role_key(),
                    "active": target.active,
                    "share": target.share,
                    "bank": target.trucalc_bank_company_id,
                    "company": target.company_id,
                    "companies": target.company_ids,
                    "groups": target.group_ids,
                    "vendor": target.trucalc_vendor_id,
                    "signup_type": target.partner_id.signup_type,
                }[field_name],
                value,
            )
        audit = self.env["trucalc.bank.admin.audit"].sudo().search([
            ("event_type", "=", "bank_user_identity_updated"),
            ("target_user_id", "=", target.id),
        ])
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit.actor_user_id, self.admin)
        self.assertEqual(audit.bank_company_id, self.bank)
        self.assertEqual(audit.metadata["changed_fields"], ["name", "phone"])
        self.assertEqual(audit.metadata["changes"]["name"], {
            "old": "5A2 identity-edit", "new": "Updated Bank User",
        })
        self.assertEqual(audit.metadata["changes"]["phone"], {
            "old": "615-555-0100", "new": "+44 20 7946 0958 x42",
        })

    def test_controlled_edit_noop_clear_and_denial_boundaries(self):
        target = self._user(
            "identity-boundaries", [self.roles["view_only"]], bank=self.bank,
        )
        target.phone = "615-555-0199"
        context = {
            "default_bank_company_id": self.bank.id,
            "default_target_user_id": target.id,
            "trucalc_locked_bank_company_id": self.bank.id,
            "trucalc_locked_target_user_id": target.id,
        }
        Edit = self.env["trucalc.bank.user.edit"].with_user(
            self.admin
        ).with_context(**context)
        noop = Edit.create({"name": target.name, "phone": target.phone})
        noop.action_save()
        self.assertFalse(self.env["trucalc.bank.admin.audit"].sudo().search_count([
            ("event_type", "=", "bank_user_identity_updated"),
            ("target_user_id", "=", target.id),
        ]))
        clear = Edit.create({"name": target.name, "phone": "   "})
        clear.action_save()
        target.invalidate_recordset()
        self.assertFalse(target.phone)
        audit = self.env["trucalc.bank.admin.audit"].sudo().search([
            ("event_type", "=", "bank_user_identity_updated"),
            ("target_user_id", "=", target.id),
        ])
        self.assertEqual(audit.metadata["changed_fields"], ["phone"])
        self.assertEqual(audit.metadata["changes"]["phone"]["new"], False)

        blank = Edit.create({"name": "   ", "phone": False})
        with self.assertRaises(ValidationError):
            blank.action_save()
        locked = Edit.create({"name": target.name, "phone": False})
        for forged_values in (
            {"owner_user_id": self.ops.id},
            {"bank_company_id": self.other_bank.id},
            {"target_user_id": self.portal.id},
            {"login": "forged@example.test"},
        ):
            with self.assertRaises(AccessError):
                locked.write(forged_values)
        with self.assertRaises(AccessError):
            locked.with_context({}).action_save()
        with self.assertRaises(AccessError):
            self.env["trucalc.bank.user.edit"].with_user(
                self.ops
            ).with_context(**context).create({
                "name": target.name, "phone": False,
            })
        cross_bank_context = dict(context)
        cross_bank_context.update({
            "default_bank_company_id": self.other_bank.id,
            "trucalc_locked_bank_company_id": self.other_bank.id,
        })
        with self.assertRaises(AccessError):
            self.env["trucalc.bank.user.edit"].with_user(
                self.admin
            ).with_context(**cross_bank_context).create({
                "name": target.name, "phone": False,
            })

    def test_active_user_email_change_preserves_identity_password_and_order(self):
        target = self._user(
            "email-active", [self.roles["requestor"]], bank=self.bank,
        )
        target.with_context(no_reset_password=True).write({
            "password": "Pass-C-active-password",
        })
        target.with_user(target)._update_last_login()
        order = self.env["trucalc.order"].sudo().create({
            "borrower": "Pass C Identity",
            "property_address": "34 Preserved History Way",
            "due_date": fields.Date.add(fields.Date.today(), days=7),
            "company_id": self.bank.id,
            "requestor_company_id": self.bank.id,
            "requestor_id": target.id,
        })
        self.env.cr.execute(
            "SELECT password FROM res_users WHERE id = %s", [target.id],
        )
        password_hash = self.env.cr.fetchone()[0]
        user_id = target.id
        partner_id = target.partner_id.id
        invariant = {
            "bank": target.trucalc_bank_company_id,
            "role": target._trucalc_bank_role_key(),
            "company": target.company_id,
            "companies": target.company_ids,
            "groups": target.group_ids,
            "active": target.active,
            "share": target.share,
            "vendor": target.trucalc_vendor_id,
        }

        wizard = self._email_change_wizard(
            target, "  Pass-C.Active@Example.TEST  ",
        )
        wizard.action_save()
        target.invalidate_recordset()
        self.assertEqual(target.id, user_id)
        self.assertEqual(target.partner_id.id, partner_id)
        self.assertEqual(target.login, "pass-c.active@example.test")
        self.assertEqual(target.email, target.login)
        self.assertEqual(order.requestor_id, target)
        self.assertEqual(order.requestor_company_id, self.bank)
        self.env.cr.execute(
            "SELECT password FROM res_users WHERE id = %s", [target.id],
        )
        self.assertEqual(self.env.cr.fetchone()[0], password_hash)
        self._assert_exact(target, self.bank, "requestor")
        self.assertEqual({
            "bank": target.trucalc_bank_company_id,
            "role": target._trucalc_bank_role_key(),
            "company": target.company_id,
            "companies": target.company_ids,
            "groups": target.group_ids,
            "active": target.active,
            "share": target.share,
            "vendor": target.trucalc_vendor_id,
        }, invariant)
        audit = self.env["trucalc.bank.admin.audit"].sudo().search([
            ("event_type", "=", "bank_user_email_updated"),
            ("target_user_id", "=", target.id),
        ])
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit.actor_user_id, self.admin)
        self.assertEqual(audit.bank_company_id, self.bank)
        self.assertEqual(audit.prior_status, "5a2-email-active@example.test")
        self.assertEqual(audit.new_status, "pass-c.active@example.test")
        self.assertEqual(audit.metadata, {
            "old_login": "5a2-email-active@example.test",
            "old_email": "5a2-email-active@example.test",
            "new_login": "pass-c.active@example.test",
            "new_email": "pass-c.active@example.test",
            "invitation_state": "confirmed",
            "pending_invite_invalidated": False,
            "outstanding_account_link_invalidated": False,
        })
        self.assertNotIn("token", str(audit.metadata).casefold())
        self.assertNotIn("password", str(audit.metadata).casefold())

    def test_pending_invitation_change_invalidates_old_and_requires_reinvite(self):
        self.main.name = "TruCalc Evaluations"
        self.main.email = "trucalc@example.test"
        target = self._user(
            "email-pending", [self.roles["administrator"]], bank=self.bank,
        )
        target.partner_id.signup_prepare(signup_type="signup")
        old_token = target.partner_id._generate_signup_token()
        denied = self._email_change_wizard(
            target, "pass-c.pending@example.test", acknowledge=False,
        )
        with self.assertRaises(ValidationError):
            denied.action_save()
        self.assertEqual(target.login, "5a2-email-pending@example.test")
        self.assertEqual(
            target.partner_id._get_partner_from_token(old_token), target.partner_id,
        )

        wizard = self._email_change_wizard(
            target, "pass-c.pending@example.test", acknowledge=True,
        )
        wizard.action_save()
        target.invalidate_recordset()
        self.assertEqual(target.login, "pass-c.pending@example.test")
        self.assertEqual(target.email, target.login)
        self.assertFalse(target.partner_id.signup_type)
        self.assertFalse(target.partner_id._get_partner_from_token(old_token))

        def successful_delivery(mails, auto_commit=False, raise_exception=False,
                                post_send_callback=None):
            mails.write({"state": "sent", "failure_type": False})
            return True

        with patch.object(MailMail, "send", autospec=True, side_effect=successful_delivery):
            result = target.with_user(self.admin)._trucalc_send_bank_invitation(
                self.bank
            )
        self.assertTrue(result["sent"])
        mail = self.env["mail.mail"].sudo().browse(result["mail_id"])
        self.assertEqual(mail.email_to, "pass-c.pending@example.test")
        signup_links = etree.HTML(mail.body_html).xpath(
            "//a[contains(@href, '/web/signup')]/@href"
        )
        self.assertEqual(len(signup_links), 1)
        new_token = parse_qs(urlparse(signup_links[0]).query)["token"][0]
        self.assertNotEqual(new_token, old_token)
        self.assertEqual(
            target.partner_id._get_partner_from_token(new_token), target.partner_id,
        )
        self.assertFalse(target.partner_id._get_partner_from_token(old_token))
        self.assertTrue(target.partner_id.signup_type.startswith("signup:"))
        audit = self.env["trucalc.bank.admin.audit"].sudo().search([
            ("event_type", "=", "bank_user_email_updated"),
            ("target_user_id", "=", target.id),
        ])
        self.assertEqual(audit.metadata["invitation_state"], "invitation_pending")
        self.assertTrue(audit.metadata["pending_invite_invalidated"])

    def test_never_invited_email_change_and_collision_validation_are_atomic(self):
        target = self._user(
            "email-never", [self.roles["view_only"]], bank=self.bank,
        )
        self._email_change_wizard(
            target, "  Pass-C.Never@Example.TEST  ",
        ).action_save()
        target.invalidate_recordset()
        self.assertEqual(target.login, "pass-c.never@example.test")
        self.assertEqual(target.email, target.login)
        self.assertFalse(target.partner_id.signup_type)
        audit = self.env["trucalc.bank.admin.audit"].sudo().search([
            ("event_type", "=", "bank_user_email_updated"),
            ("target_user_id", "=", target.id),
        ])
        self.assertEqual(audit.metadata["invitation_state"], "never_invited")
        self.assertFalse(audit.metadata["pending_invite_invalidated"])

        collision = self._user(
            "email-collision", [self.roles["requestor"]], bank=self.bank,
        )
        original = (target.login, target.email)
        cases = (
            collision.login,
            " ",
            "not-an-email",
            "two@example.test,three@example.test",
        )
        for value in cases:
            with self.assertRaises(ValidationError):
                self._email_change_wizard(target, value).action_save()
            target.invalidate_recordset()
            self.assertEqual((target.login, target.email), original)
        duplicate_partner = self.env["res.partner"].create({
            "name": "Pass C Existing Contact",
            "email": "pass-c-contact@example.test",
        })
        with self.assertRaises(ValidationError):
            self._email_change_wizard(
                target, duplicate_partner.email,
            ).action_save()
        target.invalidate_recordset()
        self.assertEqual((target.login, target.email), original)
        self.assertEqual(self.env["trucalc.bank.admin.audit"].sudo().search_count([
            ("event_type", "=", "bank_user_email_updated"),
            ("target_user_id", "=", target.id),
        ]), 1)

    def test_email_change_locked_context_and_permissions(self):
        target = self._user(
            "email-boundary", [self.roles["requestor"]], bank=self.bank,
        )
        action = self.bank.with_user(
            self.admin
        ).action_trucalc_view_bank_users()
        row = self.env["trucalc.bank.user.management"].with_user(
            self.admin
        ).search(action["domain"]).filtered(
            lambda item: item.target_user_id == target
        )
        change_action = row.action_change_email()
        self.assertEqual(
            change_action["res_model"], "trucalc.bank.user.email.change"
        )
        EmailChange = self.env["trucalc.bank.user.email.change"].with_user(
            self.admin
        ).with_context(**change_action["context"])
        defaults = EmailChange.default_get([
            "owner_user_id", "bank_company_id", "target_user_id",
            "current_login", "pending_invitation",
        ])
        self.assertEqual(defaults["owner_user_id"], self.admin.id)
        self.assertEqual(defaults["bank_company_id"], self.bank.id)
        self.assertEqual(defaults["target_user_id"], target.id)
        self.assertEqual(defaults["current_login"], target.login)
        self.assertFalse(defaults["pending_invitation"])
        wizard = EmailChange.create({"new_login": "pass-c-safe@example.test"})
        for values in (
            {"owner_user_id": self.ops.id},
            {"bank_company_id": self.other_bank.id},
            {"target_user_id": self.portal.id},
            {"current_login": "forged@example.test"},
            {"pending_invitation": True},
        ):
            with self.assertRaises(AccessError):
                wizard.write(values)
        with self.assertRaises(AccessError):
            wizard.with_context({}).action_save()
        with self.assertRaises(AccessError):
            self._email_change_wizard(
                target, "denied@example.test", actor=self.ops,
            )
        with self.assertRaises(AccessError):
            self._email_change_wizard(
                target, "cross-bank@example.test", bank=self.other_bank,
            )

    def test_equivalent_administrators_get_owned_fresh_rows_and_can_edit(self):
        admin_two = self._user("admin-two", [self.group_admin])
        admin_two.sudo().write({
            "company_id": self.main.id,
            "company_ids": [Command.set((self.main | self.bank | self.other_bank).ids)],
        })
        self.admin.sudo().write({
            "company_id": self.main.id,
            "company_ids": [Command.set((self.main | self.bank | self.other_bank).ids)],
        })
        target = self._user(
            "equivalent-admin", [self.roles["requestor"]], bank=self.bank,
        )
        action_one = self.bank.with_user(self.admin).with_context(
            allowed_company_ids=[self.main.id],
        ).action_trucalc_view_bank_users()
        action_two = self.bank.with_user(admin_two).with_context(
            allowed_company_ids=admin_two.company_ids.ids,
        ).action_trucalc_view_bank_users()
        rows_one = self.env["trucalc.bank.user.management"].with_user(
            self.admin
        ).search(action_one["domain"])
        rows_two = self.env["trucalc.bank.user.management"].with_user(
            admin_two
        ).search(action_two["domain"])
        self.assertEqual(rows_one.target_user_id, rows_two.target_user_id)
        self.assertTrue(all(row.owner_user_id == self.admin for row in rows_one))
        self.assertTrue(all(row.owner_user_id == admin_two for row in rows_two))
        self.assertFalse(
            self.env["trucalc.bank.user.management"].with_user(
                self.admin
            ).search_count([("id", "in", rows_two.ids)])
        )

        for index, actor in enumerate((self.admin, admin_two), start=1):
            wizard = self._wizard(
                "admin-%s" % index, actor=actor, phone="615-555-010%s" % index,
            )
            fresh_action = wizard.action_save()
            created = self.env["res.users"].sudo().search([
                ("login", "=", "5a2-provisioned-admin-%s@example.test" % index),
            ])
            fresh_rows = self.env["trucalc.bank.user.management"].with_user(
                actor
            ).search(fresh_action["domain"])
            self.assertIn(created, fresh_rows.target_user_id)
            created.with_user(actor)._trucalc_edit_bank_identity(
                self.bank, "Admin %s Edited" % index, "615-555-020%s" % index,
            )
            created.invalidate_recordset()
            self.assertEqual(created.name, "Admin %s Edited" % index)
            self.assertEqual(created.phone, "(615) 555-020%s" % index)

        self.assertEqual(target.trucalc_bank_company_id, self.bank)

    def test_missing_bank_launch_has_no_partial_identity_or_audit(self):
        before = {
            "users": self.env["res.users"].sudo().with_context(
                active_test=False
            ).search_count([]),
            "partners": self.env["res.partner"].sudo().with_context(
                active_test=False
            ).search_count([]),
            "audits": self.env["trucalc.bank.admin.audit"].sudo().search_count([
                ("event_type", "=", "bank_user_created"),
            ]),
        }
        with self.assertRaises(ValidationError):
            self.env["trucalc.bank.user.provision"].with_user(self.admin).create({
                "name": "5A2 Missing Bank",
                "login": "5a2-missing-bank@example.test",
                "role": "requestor",
            })
        self.assertEqual(
            self.env["res.users"].sudo().with_context(active_test=False).search_count([]),
            before["users"],
        )
        self.assertEqual(
            self.env["res.partner"].sudo().with_context(active_test=False).search_count([]),
            before["partners"],
        )
        self.assertEqual(
            self.env["trucalc.bank.admin.audit"].sudo().search_count([
                ("event_type", "=", "bank_user_created"),
            ]),
            before["audits"],
        )

    def test_authority_and_bank_state_fail_closed(self):
        bank_user = self._user("bank-actor", [self.roles["administrator"]], bank=self.bank)
        for index, actor in enumerate((
            self.ops, self.reviewer, self.internal, self.portal, self.vendor_user, bank_user,
        )):
            with self.assertRaises(AccessError):
                self._wizard("denied-%s" % index, actor=actor).action_save()
        inactive = self.env["res.company"].with_context(trucalc_test_bank_fixture=True).create({
            "name": "5A2 Inactive Bank", "currency_id": self.main.currency_id.id,
            "trucalc_is_bank": True, "trucalc_bank_active": False,
        })
        with self.assertRaises(AccessError):
            self._wizard("inactive", bank=inactive).action_save()
        with self.assertRaises(AccessError):
            self._wizard("main", bank=self.main).action_save()
        with self.assertRaises(AccessError):
            inactive.with_user(self.admin).action_trucalc_add_bank_user()
        with self.assertRaises(AccessError):
            self.bank.with_user(self.ops).action_trucalc_add_bank_user()

    def test_plain_portal_reuse_and_collision_rejections_are_unchanged(self):
        login = "5a2-reuse@example.test"
        plain = self._user("reuse", [self.env.ref("base.group_portal")], login=login)
        before_id = plain.id
        self.env["trucalc.bank.user.provision"].with_user(self.admin).create({
            "bank_company_id": self.bank.id, "name": "Reused Portal",
            "login": login.upper(), "role": "requestor", "active": True,
        }).action_save()
        plain.invalidate_recordset()
        self.assertEqual(plain.id, before_id)
        self._assert_exact(plain, self.bank, "requestor")

        same = self._user("same", [self.roles["administrator"]], bank=self.bank)
        foreign = self._user("foreign", [self.roles["requestor"]], bank=self.other_bank)
        archived = self._user("archived", [self.roles["view_only"]], bank=self.bank, active=False)
        for target in (same, foreign, archived, self.vendor_user, self.internal):
            snapshot = (target.active, target.group_ids.ids, target.trucalc_bank_company_id.id)
            with self.assertRaises(ValidationError):
                self.env["res.users"].with_user(self.admin)._trucalc_provision_bank_user(
                    self.bank, "Collision", target.login, "requestor", True,
                )
            target.invalidate_recordset()
            self.assertEqual(
                (target.active, target.group_ids.ids, target.trucalc_bank_company_id.id), snapshot
            )

    def test_partner_only_reuse_duplicates_and_partner_mismatch(self):
        partner = self.env["res.partner"].create({
            "name": "5A2 Partner Only", "email": "5a2-partner-only@example.test",
        })
        user = self.env["res.users"].with_user(self.admin)._trucalc_provision_bank_user(
            self.bank, "Partner Reused", partner.email, "view_only", True,
        )
        self.assertEqual(user.partner_id, partner)
        self._assert_exact(user, self.bank, "view_only")

        mismatch = self._user(
            "partner-mismatch", [self.env.ref("base.group_portal")],
            login="5a2-mismatch@example.test",
        )
        mismatch.partner_id.email = "different@example.test"
        with self.assertRaises(ValidationError):
            self.env["res.users"].with_user(self.admin)._trucalc_provision_bank_user(
                self.bank, "Mismatch", mismatch.login, "requestor", True,
            )
        duplicate_email = "5a2-duplicate-contact@example.test"
        self.env["res.partner"].create({"name": "Duplicate One", "email": duplicate_email})
        self.env["res.partner"].create({"name": "Duplicate Two", "email": duplicate_email})
        with self.assertRaises(ValidationError):
            self.env["res.users"].with_user(self.admin)._trucalc_provision_bank_user(
                self.bank, "Duplicate", duplicate_email, "requestor", True,
            )

    def test_role_deactivate_reactivate_and_audit(self):
        target = self._user("lifecycle", [self.roles["requestor"]], bank=self.bank)
        target.with_user(self.admin)._trucalc_change_bank_role(self.bank, "administrator")
        self._assert_exact(target, self.bank, "administrator")
        target.partner_id.sudo().signup_prepare()
        self.assertTrue(target.partner_id.signup_type)
        target.with_user(self.admin)._trucalc_deactivate_bank_user(self.bank)
        target.invalidate_recordset()
        self.assertFalse(target.active)
        self.assertFalse(target.partner_id.signup_type)
        target.with_user(self.admin)._trucalc_reactivate_bank_user(self.bank)
        self._assert_exact(target, self.bank, "administrator")
        self.assertFalse(target.partner_id.signup_type)
        events = self.env["trucalc.bank.admin.audit"].sudo().search([
            ("target_user_id", "=", target.id),
        ]).mapped("event_type")
        self.assertIn("role_changed", events)
        self.assertIn("user_deactivated", events)
        self.assertIn("user_reactivated", events)
        with self.assertRaises(AccessError):
            target.with_user(self.ops)._trucalc_change_bank_role(self.bank, "view_only")
        with self.assertRaises(AccessError):
            target.with_user(self.admin)._trucalc_change_bank_role(self.other_bank, "view_only")

    def test_inactive_bank_blocks_reactivation(self):
        target = self._user("inactive-bank-user", [self.roles["requestor"]], bank=self.bank)
        target.with_user(self.admin)._trucalc_deactivate_bank_user(self.bank)
        self.bank.with_user(self.admin).action_trucalc_deactivate_bank()
        with self.assertRaises(AccessError):
            target.with_user(self.admin)._trucalc_reactivate_bank_user(self.bank)

    def test_invitation_uses_signed_token_branding_resend_and_safe_failure(self):
        self.main.name = "TruCalc Evaluations"
        self.main.email = "trucalc@example.test"
        target = self._user("invite", [self.roles["requestor"]], bank=self.bank)
        template = self.env.ref("trucalc_orders.mail_template_bank_user_invitation")
        self.assertIn("TruCalc", template.name)
        self.assertIn("base.main_company", template.email_from)

        def failed_delivery(mails, auto_commit=False, raise_exception=False,
                            post_send_callback=None):
            self.assertFalse(raise_exception)
            mails.write({
                "state": "exception",
                "failure_type": "mail_smtp",
                "failure_reason": "Simulated SMTP connection failure",
            })
            return True

        action = self.bank.with_user(self.admin).action_trucalc_view_bank_users()
        rows = self.env["trucalc.bank.user.management"].with_user(self.admin).search(
            action["domain"]
        )
        row = rows.filtered(lambda item: item.target_user_id == target)
        self.assertEqual(len(row), 1)
        with patch.object(MailMail, "send", autospec=True, side_effect=failed_delivery):
            notification = row.action_send_invitation()

        self.assertEqual(notification["params"]["type"], "danger")
        self.assertTrue(notification["params"]["sticky"])
        self.assertEqual(notification["params"]["next"]["tag"], "reload")
        self.assertIn("retained", notification["params"]["message"])
        self.assertIn("Resend remains available", notification["params"]["message"])
        target.invalidate_recordset()
        row.invalidate_recordset()
        self._assert_exact(target, self.bank, "requestor")
        self.assertTrue(target.partner_id.signup_type.startswith("signup:"))
        self.assertEqual(row.invitation_state, "pending")
        failed_audit = self.env["trucalc.bank.admin.audit"].sudo().search([
            ("event_type", "=", "invitation_failed"),
            ("target_user_id", "=", target.id),
        ])
        self.assertEqual(failed_audit.mail_state, "exception")
        self.assertEqual(failed_audit.metadata["delivery_outcome"], "failed")

        failed_mail = self.env["mail.mail"].sudo().search([
            ("mail_message_id.model", "=", "res.users"),
            ("mail_message_id.res_id", "=", target.id),
        ])
        self.assertEqual(len(failed_mail), 1)
        self.assertEqual(failed_mail.state, "exception")
        self.assertEqual(failed_mail.email_to, target.email)
        self.assertEqual(failed_mail.email_from, self.main.email_formatted)
        self.assertEqual(failed_mail.mail_message_id.message_type, "email_outgoing")
        self.assertEqual(failed_mail.mail_message_id.subject, "Set up your TruCalc account")
        self.assertIn("Welcome to TruCalc", failed_mail.body_html)
        self.assertIn("Set Your TruCalc Password", failed_mail.body_html)
        self.assertNotIn("password=", failed_mail.body_html.casefold())
        ctas = etree.HTML(failed_mail.body_html).xpath(
            "//a[contains(@href, '/web/signup')]"
        )
        self.assertEqual(len(ctas), 1)
        cta = ctas[0]
        self.assertEqual(" ".join(cta.itertext()).strip(), "Set Your TruCalc Password")
        styles = {
            name.strip().casefold(): value.strip().casefold()
            for declaration in cta.get("style", "").split(";")
            if ":" in declaration
            for name, value in [declaration.split(":", 1)]
        }
        self.assertEqual(styles["background-color"], "#022f5b")
        self.assertEqual(styles["color"], "#ffffff")
        self.assertNotEqual(styles["background-color"], styles["color"])
        self.assertEqual(styles["display"], "inline-block")
        self.assertEqual(styles["padding"], "10px")
        self.assertEqual(styles["text-decoration"], "none")
        self.assertFalse(cta.get("class"))
        token = parse_qs(urlparse(cta.get("href")).query)["token"][0]
        self.assertEqual(
            target.partner_id.sudo()._get_partner_from_token(token),
            target.partner_id,
        )

        def successful_delivery(mails, auto_commit=False, raise_exception=False,
                                post_send_callback=None):
            self.assertFalse(raise_exception)
            mails.write({"state": "sent", "failure_type": False, "failure_reason": False})
            return True

        with patch.object(MailMail, "send", autospec=True, side_effect=successful_delivery):
            first_success = row.action_send_invitation()
            second_success = row.action_send_invitation()
        self.assertEqual(first_success["params"]["type"], "success")
        self.assertEqual(second_success["params"]["type"], "success")
        with patch.object(MailMail, "send", autospec=True, side_effect=failed_delivery):
            failed_resend = row.action_send_invitation()
        self.assertEqual(failed_resend["params"]["type"], "danger")
        mails = self.env["mail.mail"].sudo().search([
            ("mail_message_id.model", "=", "res.users"),
            ("mail_message_id.res_id", "=", target.id),
        ], order="id")
        self.assertEqual(len(mails), 4)
        self.assertEqual(
            mails.mapped("state"), ["exception", "sent", "sent", "exception"]
        )
        self.assertTrue(all(not mail.auto_delete for mail in mails))
        for index, mail in enumerate(mails):
            self.assertEqual(mail.email_to, target.email)
            self.assertEqual(mail.email_from, self.main.email_formatted)
            self.assertIn("TruCalc", mail.email_from)
            self.assertIn("TruCalc", mail.mail_message_id.subject)
            self.assertIn("Welcome to TruCalc", mail.body_html)
            signup_links = etree.HTML(mail.body_html).xpath(
                "//a[contains(@href, '/web/signup')]/@href"
            )
            self.assertEqual(len(signup_links), 1)
            signup_token = parse_qs(urlparse(signup_links[0]).query)["token"][0]
            resolved = target.partner_id.sudo()._get_partner_from_token(
                signup_token
            )
            if index == len(mails) - 1:
                self.assertEqual(resolved, target.partner_id)
            else:
                self.assertFalse(resolved)
        for actor in (self.ops, self.reviewer, self.portal, self.vendor_user):
            with self.assertRaises(AccessError):
                target.with_user(actor)._trucalc_send_bank_invitation(self.bank)
        with self.assertRaises(AccessError):
            target.with_user(self.admin)._trucalc_send_bank_invitation(self.other_bank)
        self.assertEqual(
            self.env["trucalc.bank.admin.audit"].sudo().search_count([
                ("event_type", "=", "invitation_resent"),
                ("target_user_id", "=", target.id),
            ]), 2,
        )

    def test_confirmed_user_requires_reset_and_reset_nonce_invalidates_old_link(self):
        self.main.email = "trucalc@example.test"
        target = self._user("confirmed-reset", [self.roles["requestor"]], bank=self.bank)
        target.with_context(no_reset_password=True).write({"password": "Still-Valid-Password"})
        target.with_user(target)._update_last_login()
        self.env.cr.execute("SELECT password FROM res_users WHERE id = %s", [target.id])
        password_hash = self.env.cr.fetchone()[0]

        with self.assertRaisesRegex(ValidationError, "never been invited"):
            target.with_user(self.admin)._trucalc_send_bank_invitation(self.bank)

        def sent(mails, auto_commit=False, raise_exception=False,
                 post_send_callback=None):
            mails.write({"state": "sent", "failure_type": False})
            return True

        with patch.object(MailMail, "send", autospec=True, side_effect=sent):
            first = target.with_user(self.admin)._trucalc_send_bank_password_reset(
                self.bank
            )
        first_mail = self.env["mail.mail"].sudo().browse(first["mail_id"])
        first_link = etree.HTML(first_mail.body_html).xpath(
            "//a[contains(@href, '/web/reset_password')]/@href"
        )[0]
        first_token = parse_qs(urlparse(first_link).query)["token"][0]
        self.assertTrue(target.partner_id.signup_type.startswith("reset:"))
        self.assertEqual(target._trucalc_bank_account_state(self.bank), "reset_pending")
        self.assertEqual(
            target.partner_id.sudo()._get_partner_from_token(first_token),
            target.partner_id,
        )
        self.env.cr.execute("SELECT password FROM res_users WHERE id = %s", [target.id])
        self.assertEqual(self.env.cr.fetchone()[0], password_hash)

        with patch.object(MailMail, "send", autospec=True, side_effect=sent):
            second = target.with_user(self.admin)._trucalc_send_bank_password_reset(
                self.bank
            )
        second_mail = self.env["mail.mail"].sudo().browse(second["mail_id"])
        second_token = parse_qs(urlparse(etree.HTML(second_mail.body_html).xpath(
            "//a[contains(@href, '/web/reset_password')]/@href"
        )[0]).query)["token"][0]
        self.assertFalse(target.partner_id.sudo()._get_partner_from_token(first_token))
        self.assertEqual(
            target.partner_id.sudo()._get_partner_from_token(second_token),
            target.partner_id,
        )
        audit = self.env["trucalc.bank.admin.audit"].sudo().search([
            ("target_user_id", "=", target.id),
            ("event_type", "=", "password_reset_sent"),
        ], order="id desc", limit=1)
        self.assertEqual(audit.metadata["validity_hours"], 4)
        self.assertEqual(audit.metadata["link_kind"], "reset")
        self.assertNotIn("token", str(audit.metadata).casefold())
        self.assertNotIn("/web/", str(audit.metadata).casefold())

    def test_expired_bank_invitation_is_resend_eligible(self):
        self.main.email = "trucalc@example.test"
        target = self._user("expired-invite", [self.roles["requestor"]], bank=self.bank)
        self.env["ir.config_parameter"].sudo().set_param(
            "auth_signup.signup.validity.hours", -1,
        )

        def sent(mails, auto_commit=False, raise_exception=False,
                 post_send_callback=None):
            mails.write({"state": "sent", "failure_type": False})
            return True

        with patch.object(MailMail, "send", autospec=True, side_effect=sent):
            target.with_user(self.admin)._trucalc_send_bank_invitation(self.bank)
        self.assertEqual(
            target._trucalc_bank_account_state(self.bank), "invitation_expired",
        )
        with patch.object(MailMail, "send", autospec=True, side_effect=sent):
            result = target.with_user(
                self.admin
            )._trucalc_resend_bank_invitation(self.bank)
        self.assertTrue(result["sent"])

    def test_management_projection_is_scoped_and_no_broad_users_acl(self):
        local = self._user("local-list", [self.roles["requestor"]], bank=self.bank)
        foreign = self._user("foreign-list", [self.roles["requestor"]], bank=self.other_bank)
        form = etree.fromstring(
            self.env.ref("trucalc_orders.view_trucalc_bank_form").arch
        )
        edit_buttons = form.xpath(
            ".//header/button[@name='action_trucalc_view_bank_users']"
        )
        self.assertEqual(len(edit_buttons), 1)
        self.assertEqual(edit_buttons[0].get("string"), "Edit Users")
        self.assertEqual(edit_buttons[0].get("class"), "btn-primary")
        self.assertEqual(
            edit_buttons[0].get("groups"), "trucalc_orders.group_trucalc_admin"
        )
        self.assertFalse(edit_buttons[0].get("invisible"))
        stat_buttons = form.xpath(
            ".//div[@name='button_box']/button[@name='action_trucalc_view_bank_users']"
        )
        self.assertEqual(len(stat_buttons), 1)
        self.assertEqual(self.bank.trucalc_bank_user_count, 1)

        action = self.bank.with_user(self.admin).action_trucalc_view_bank_users()
        self.assertEqual(action["res_model"], "trucalc.bank.user.management")
        self.assertEqual(action["view_mode"], "list")
        self.assertEqual(
            action["views"][0][0],
            self.env.ref("trucalc_orders.view_trucalc_bank_user_management_list").id,
        )
        rows = self.env["trucalc.bank.user.management"].with_user(self.admin).search(action["domain"])
        self.assertIn(local, rows.target_user_id)
        self.assertNotIn(foreign, rows.target_user_id)
        self.assertEqual(action["context"]["default_bank_company_id"], self.bank.id)
        self.assertEqual(action["context"]["active_bank_company_id"], self.bank.id)
        self.assertFalse(
            self.env["res.users"].with_user(self.admin).has_access("write")
            and self.env["res.users"].with_user(self.admin).has_access("create")
        )
        arch = etree.fromstring(
            self.env.ref("trucalc_orders.view_trucalc_bank_user_management_list").arch
        )
        self.assertTrue(arch.xpath(".//field[@name='phone']"))
        self.assertEqual(
            len(arch.xpath(
                ".//field[@name='name'][@widget='trucalc_bank_user_manage_link']"
            )),
            1,
        )
        self.assertFalse(arch.xpath(".//button[@type='object']"))
        self.assertFalse(set(arch.xpath(".//field/@name")) & {
            "password", "group_ids", "company_id", "company_ids",
            "trucalc_bank_company_id", "trucalc_vendor_id",
        })
        manage_arch = etree.fromstring(
            self.env.ref("trucalc_orders.view_trucalc_bank_user_manage_form").arch
        )
        self.assertTrue(manage_arch.xpath(
            ".//button[@name='action_save'][@string='Save Changes']"
        ))
        self.assertEqual(set(manage_arch.xpath(
            ".//group[@string='Account Actions']/button/@name"
        )), {
            "action_send_invitation", "action_resend_invitation",
            "action_send_password_reset", "action_reactivate",
            "action_deactivate",
        })
        visibility = {
            button.get("name"): button.get("invisible")
            for button in manage_arch.xpath(
                ".//group[@string='Account Actions']/button"
            )
        }
        self.assertEqual(
            visibility["action_send_invitation"],
            "account_state != 'never_invited'",
        )
        self.assertIn("invitation_pending", visibility["action_resend_invitation"])
        self.assertIn("invitation_expired", visibility["action_resend_invitation"])
        self.assertIn("confirmed", visibility["action_send_password_reset"])
        self.assertIn("reset_pending", visibility["action_send_password_reset"])
        self.assertEqual(
            visibility["action_reactivate"], "account_state != 'inactive'",
        )
        self.assertTrue(manage_arch.xpath(
            ".//button[@name='action_deactivate'][@confirm]"
        ))
        self.assertFalse(set(manage_arch.xpath(".//field/@name")) & {
            "password", "group_ids", "company_id", "company_ids", "share",
            "trucalc_bank_company_id", "trucalc_vendor_id",
        })
        with self.assertRaises(AccessError):
            self.other_bank.with_user(self.ops).action_trucalc_view_bank_users()

        inactive = self.env["res.company"].with_context(
            trucalc_test_bank_fixture=True
        ).create({
            "name": "5A2 Inactive Users Bank",
            "currency_id": self.main.currency_id.id,
            "trucalc_is_bank": True,
            "trucalc_bank_active": False,
        })
        inactive_user = self._user(
            "inactive-list", [self.roles["view_only"]], bank=inactive
        )
        inactive_action = inactive.with_user(
            self.admin
        ).action_trucalc_view_bank_users()
        inactive_rows = self.env["trucalc.bank.user.management"].with_user(
            self.admin
        ).search(inactive_action["domain"])
        self.assertEqual(inactive_rows.target_user_id, inactive_user)
        with self.assertRaises(AccessError):
            inactive.with_user(self.admin).action_trucalc_add_bank_user()
        for actor in (
            self.ops, self.reviewer, self.internal, self.portal, self.vendor_user,
        ):
            with self.assertRaises(AccessError):
                inactive.with_user(actor).action_trucalc_view_bank_users()
