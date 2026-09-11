from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from lxml import etree

from odoo import Command
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

    def _wizard(self, suffix, role="requestor", bank=False, active=True, actor=False):
        actor = actor or self.admin
        return self.env["trucalc.bank.user.provision"].with_user(actor).create({
            "bank_company_id": (bank or self.bank).id,
            "name": "5A2 Provisioned %s" % suffix,
            "login": "5a2-provisioned-%s@example.test" % suffix,
            "role": role, "active": active,
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
        self.assertEqual(target.partner_id.signup_type, "signup")
        self.assertEqual(row.invitation_state, "pending")
        self.assertFalse(self.env["trucalc.bank.admin.audit"].sudo().search_count([
            ("event_type", "=", "invitation_sent"),
            ("target_user_id", "=", target.id),
        ]))

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
        for mail in mails:
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
            self.assertEqual(
                target.partner_id.sudo()._get_partner_from_token(signup_token),
                target.partner_id,
            )
        for actor in (self.ops, self.reviewer, self.portal, self.vendor_user):
            with self.assertRaises(AccessError):
                target.with_user(actor)._trucalc_send_bank_invitation(self.bank)
        with self.assertRaises(AccessError):
            target.with_user(self.admin)._trucalc_send_bank_invitation(self.other_bank)
        self.assertEqual(
            self.env["trucalc.bank.admin.audit"].sudo().search_count([
                ("event_type", "=", "invitation_sent"),
                ("target_user_id", "=", target.id),
            ]), 2,
        )

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
        self.assertFalse(set(arch.xpath(".//field/@name")) & {
            "password", "group_ids", "company_id", "company_ids", "trucalc_vendor_id",
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
