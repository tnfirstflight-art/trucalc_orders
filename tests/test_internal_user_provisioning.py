from unittest.mock import patch

from lxml import etree

from odoo import Command, fields
from odoo.addons.mail.models.mail_mail import MailMail
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged

from ..models.res_users import ResUsers


@tagged("post_install", "-at_install", "trucalc_internal_user_provisioning")
class TestInternalUserProvisioning(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.main = cls.env.ref("base.main_company")
        cls.roles = {
            "administrator": cls.env.ref("trucalc_orders.group_trucalc_admin"),
            "operations": cls.env.ref("trucalc_orders.group_trucalc_operations"),
        }
        cls.reviewer_group = cls.env.ref("trucalc_orders.group_trucalc_reviewer")
        Companies = cls.env["res.company"].with_context(
            trucalc_test_bank_fixture=True
        )
        cls.bank = Companies.create({
            "name": "Internal Users Active Bank",
            "currency_id": cls.main.currency_id.id,
            "trucalc_is_bank": True,
            "trucalc_bank_active": True,
        })
        cls.inactive_bank = Companies.create({
            "name": "Internal Users Inactive Bank",
            "currency_id": cls.main.currency_id.id,
            "trucalc_is_bank": True,
            "trucalc_bank_active": False,
        })
        cls.unrelated = Companies.create({
            "name": "Internal Users Unrelated Company",
            "currency_id": cls.main.currency_id.id,
        })
        cls.admin = cls._internal_user(
            "actor-admin", "administrator", reviewer=False,
        )
        cls.ops = cls._internal_user("actor-ops", "operations")
        cls.reviewer = cls._internal_user(
            "actor-reviewer", "operations", reviewer=True,
        )
        cls.portal = cls._plain_user(
            "actor-portal", cls.env.ref("base.group_portal"), share=True,
        )
        cls.vendor = cls.env["trucalc.vendor"].create({
            "name": "Internal Users Vendor",
        })
        cls.vendor_user = cls._plain_user(
            "actor-vendor", cls.env.ref("trucalc_orders.group_vendor_portal"),
            share=True, vendor=cls.vendor,
        )

    @classmethod
    def _companies(cls):
        return cls.main | cls.env["res.company"].with_context(
            active_test=False
        ).search([("trucalc_is_bank", "=", True)])

    @classmethod
    def _internal_user(cls, suffix, role, reviewer=False, active=True):
        groups = cls.roles[role]
        if reviewer:
            groups |= cls.reviewer_group
        login = "internal-users-%s@example.test" % suffix
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Internal Users %s" % suffix,
            "login": login,
            "email": login,
            "active": active,
            "share": False,
            "company_id": cls.main.id,
            "company_ids": [Command.set(cls._companies().ids)],
            "group_ids": [Command.set(groups.ids)],
        })

    @classmethod
    def _plain_user(cls, suffix, group, share=False, bank=False, vendor=False):
        login = "internal-users-%s@example.test" % suffix
        values = {
            "name": "Internal Users %s" % suffix,
            "login": login,
            "email": login,
            "share": share,
            "group_ids": [Command.set([group.id])],
        }
        if bank:
            values.update({
                "trucalc_bank_company_id": bank.id,
                "company_id": bank.id,
                "company_ids": [Command.set([bank.id])],
            })
        if vendor:
            values["trucalc_vendor_id"] = vendor.id
        return cls.env["res.users"].with_context(no_reset_password=True).create(values)

    def _provision(self, suffix, role="operations", reviewer=False, phone=False):
        return self.env["res.users"].with_user(
            self.admin
        )._trucalc_provision_internal_user(
            "Provisioned %s" % suffix,
            "internal-provisioned-%s@example.test" % suffix,
            phone, role, reviewer,
        )

    def _assert_managed(self, user, role, reviewer=False, active=True):
        user = user.sudo().with_context(active_test=False)
        self.assertEqual(user.active, active)
        self.assertFalse(user.share)
        self.assertEqual(user._trucalc_internal_role_key(), role)
        self.assertEqual(
            bool(user._trucalc_persona_membership()["reviewer"]), reviewer,
        )
        self.assertEqual(user.company_id, self.main)
        self.assertEqual(user.company_ids, self._companies())
        self.assertFalse(user.trucalc_bank_company_id)
        self.assertFalse(user.trucalc_vendor_id)
        if active:
            self.assertEqual(
                user.action_id.id,
                self.env.ref("trucalc_orders.action_trucalc_orders").id,
            )

    def _audit_count(self, user, event_type):
        return self.env["trucalc.internal.user.admin.audit"].sudo().search_count([
            ("target_user_id", "=", user.id),
            ("event_type", "=", event_type),
        ])

    def _order(self, reviewer, status):
        order = self.env["trucalc.order"].with_user(self.admin).create({
            "borrower": "Internal user review",
            "property_address": "1 Review Way",
            "company_id": self.bank.id,
            "service_type": "evaluation",
            "due_date": fields.Date.add(fields.Date.today(), days=5),
        })
        order._controlled_lifecycle_write({
            "reviewer_user_id": reviewer.id,
            "status": status,
        })
        return order

    def _manage_wizard(self, target, actor=False):
        actor = actor or self.admin
        action = self.env[
            "trucalc.internal.user.management"
        ].with_user(actor)._trucalc_open()
        row = self.env["trucalc.internal.user.management"].with_user(
            actor
        ).search(action["domain"]).filtered(
            lambda item: item.target_user_id == target
        )
        self.assertEqual(len(row), 1)
        manage_action = row.action_manage()
        self.assertEqual(
            manage_action["res_model"], "trucalc.internal.user.manage",
        )
        self.assertEqual(manage_action["target"], "new")
        return self.env["trucalc.internal.user.manage"].with_user(
            actor
        ).with_context(**manage_action["context"]).browse(
            manage_action["res_id"]
        )

    def test_provisions_both_roles_reviewer_phone_companies_and_home(self):
        for role, reviewer in (
            ("administrator", False), ("administrator", True),
            ("operations", False), ("operations", True),
        ):
            user = self._provision(
                "%s-%s" % (role, reviewer), role, reviewer, "+1 555 0100",
            )
            self._assert_managed(user, role, reviewer)
            self.assertEqual(user.phone, "+1 555 0100")
            self.assertFalse(user.partner_id.signup_type)
            self.assertNotIn(self.env.ref("base.group_system"), user.all_group_ids)
            self.assertNotIn(
                self.env.ref("project.group_project_manager"), user.all_group_ids,
            )
            audit = self.env["trucalc.internal.user.admin.audit"].sudo().search([
                ("target_user_id", "=", user.id),
                ("event_type", "=", "user_provisioned"),
            ])
            self.assertEqual(audit.actor_user_id, self.admin)
            self.assertEqual(audit.new_role, role)
            self.assertEqual(audit.new_reviewer, reviewer)

    def test_company_selector_scope_preserves_company_memberships(self):
        admin_reviewer = self._internal_user(
            "selector-admin-reviewer", "administrator", reviewer=True,
        )
        clean_users = (self.admin, self.ops, admin_reviewer, self.reviewer)
        company_state = {
            user.id: (user.company_id.id, tuple(user.company_ids.ids))
            for user in clean_users
        }
        for user in clean_users:
            self.assertTrue(user._trucalc_should_hide_company_selector())
            self.assertEqual(
                (user.company_id.id, tuple(user.company_ids.ids)),
                company_state[user.id],
            )

        generic = self._plain_user(
            "selector-generic", self.env.ref("base.group_user"),
        )
        generic.company_ids = [Command.set(self._companies().ids)]
        system = self.env["res.users"].with_context(
            no_reset_password=True
        ).create({
            "name": "Internal Users selector-system",
            "login": "internal-users-selector-system@example.test",
            "email": "internal-users-selector-system@example.test",
            "company_id": self.main.id,
            "company_ids": [Command.set(self._companies().ids)],
            "group_ids": [Command.set([
                self.roles["administrator"].id,
                self.env.ref("base.group_system").id,
            ])],
        })
        for user in (
            generic, system, self.portal, self.vendor_user,
        ):
            self.assertFalse(user._trucalc_should_hide_company_selector())

    def test_internal_phone_us_formatting_and_safe_preservation(self):
        cases = (
            ("6625551212", "(662) 555-1212"),
            ("662-555-1212", "(662) 555-1212"),
            ("(662) 555-1212", "(662) 555-1212"),
            ("662 555 1212", "(662) 555-1212"),
            (False, False),
            ("+44 20 7946 0958", "+44 20 7946 0958"),
            ("6625551212 ext 7", "(662) 555-1212 ext. 7"),
        )
        for index, (value, expected) in enumerate(cases):
            user = self._provision(
                "phone-format-%s" % index, phone=value,
            )
            self.assertEqual(user.phone, expected)

        wizard = self.env["trucalc.internal.user.provision"].with_user(
            self.admin
        ).new({"phone": "662.555.1212"})
        wizard._onchange_phone()
        self.assertEqual(wizard.phone, "(662) 555-1212")

        raw_existing = self._internal_user("raw-phone-display", "operations")
        raw_existing.phone = "6625551212"
        action = self.env[
            "trucalc.internal.user.management"
        ].with_user(self.admin)._trucalc_open_user(raw_existing)
        projection = self.env[
            "trucalc.internal.user.manage"
        ].with_user(self.admin).with_context(**action["context"]).browse(
            action["res_id"]
        )
        self.assertEqual(projection.phone, "(662) 555-1212")

    def test_partner_reuse_and_identity_collisions_fail_closed(self):
        partner = self.env["res.partner"].create({
            "name": "Reusable Internal Contact",
            "email": "internal-partner-reuse@example.test",
        })
        user = self.env["res.users"].with_user(
            self.admin
        )._trucalc_provision_internal_user(
            "Reused Internal Contact", partner.email.upper(), False,
            "operations", False,
        )
        self.assertEqual(user.partner_id, partner)
        self.assertEqual(
            set(self.env["trucalc.internal.user.admin.audit"].sudo().search([
                ("target_user_id", "=", user.id),
            ]).mapped("event_type")),
            {"user_provisioned", "existing_partner_reused"},
        )

        generic = self._plain_user("generic", self.env.ref("base.group_user"))
        bank_user = self._plain_user(
            "bank", self.env.ref("trucalc_orders.group_bank_requestor"),
            share=True, bank=self.bank,
        )
        for target in (generic, bank_user, self.vendor_user, user):
            with self.assertRaises(ValidationError):
                self.env["res.users"].with_user(
                    self.admin
                )._trucalc_provision_internal_user(
                    "Collision", target.login, False, "operations", False,
                )

        inactive = self._internal_user(
            "inactive-collision", "operations", active=False,
        )
        system_user = self._plain_user(
            "system-collision", self.env.ref("base.group_system"),
        )
        externally_mapped = self._plain_user(
            "external-mapping-collision", self.env.ref("base.group_user"),
            vendor=self.vendor,
        )
        arbitrary_company = self._internal_user(
            "arbitrary-company-collision", "operations",
        )
        arbitrary_company.company_ids = [Command.set([
            self.main.id, self.unrelated.id,
        ])]
        for target in (
            inactive, system_user, externally_mapped, arbitrary_company,
        ):
            with self.assertRaises(ValidationError):
                self.env["res.users"].with_user(
                    self.admin
                )._trucalc_provision_internal_user(
                    "Collision", target.login, False, "operations", False,
                )

        mismatch = self._plain_user(
            "login-mismatch", self.env.ref("base.group_user"),
        )
        mismatch.partner_id.email = "different-address@example.test"
        with self.assertRaises(ValidationError):
            self.env["res.users"].with_user(
                self.admin
            )._trucalc_provision_internal_user(
                "Mismatch", mismatch.login, False, "operations", False,
            )

        duplicate_user_identity = "internal-duplicate-users@example.test"
        duplicate_users = self.env["res.users"].browse()
        for suffix in ("a", "b"):
            duplicate_users |= self._plain_user(
                "duplicate-user-%s" % suffix,
                self.env.ref("base.group_user"),
            )
        duplicate_users.partner_id.write({"email": duplicate_user_identity})
        with self.assertRaises(ValidationError):
            self.env["res.users"].with_user(
                self.admin
            )._trucalc_provision_internal_user(
                "Duplicate Users", duplicate_user_identity, False,
                "operations", False,
            )

        mixed = self._internal_user("mixed-persona", "operations")
        self.env.cr.execute(
            "INSERT INTO res_groups_users_rel (uid, gid) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING",
            (mixed.id, self.roles["administrator"].id),
        )
        self.env.invalidate_all()
        with self.assertRaises(ValidationError):
            self.env["res.users"].with_user(
                self.admin
            )._trucalc_provision_internal_user(
                "Mixed", mixed.login, False, "operations", False,
            )

        duplicate = "internal-duplicate-partner@example.test"
        self.env["res.partner"].create({"name": "Duplicate A", "email": duplicate})
        self.env["res.partner"].create({"name": "Duplicate B", "email": duplicate})
        with self.assertRaises(ValidationError):
            self.env["res.users"].with_user(
                self.admin
            )._trucalc_provision_internal_user(
                "Duplicate", duplicate, False, "operations", False,
            )

    def test_authority_is_server_enforced(self):
        for index, actor in enumerate((
            self.ops, self.reviewer, self.portal, self.vendor_user,
        )):
            with self.assertRaises(AccessError):
                self.env["res.users"].with_user(
                    actor
                )._trucalc_provision_internal_user(
                    "Denied", "internal-denied-%s@example.test" % index,
                    False, "operations", False,
                )

    def test_role_change_preserves_reviewer_and_last_admin_guard(self):
        target = self._provision("role-change", "administrator", True)
        target.with_user(self.admin)._trucalc_change_internal_role("operations")
        self._assert_managed(target, "operations", True)
        self.assertEqual(self._audit_count(target, "role_changed"), 1)
        target.with_user(self.admin)._trucalc_change_internal_role("administrator")
        self._assert_managed(target, "administrator", True)
        self.assertEqual(self._audit_count(target, "role_changed"), 2)
        with patch.object(
            ResUsers, "_trucalc_active_internal_administrators",
            autospec=True, return_value=target,
        ):
            with self.assertRaises(ValidationError):
                target.with_user(self.admin)._trucalc_change_internal_role(
                    "operations"
                )
        other_admin = self._provision("self-demotion-backstop", "administrator", False)
        self.assertTrue(other_admin.active)
        with self.assertRaisesRegex(ValidationError, "your own"):
            self.admin.with_user(self.admin)._trucalc_change_internal_role("operations")

    def test_reviewer_toggle_blocks_active_work_but_allows_history(self):
        target = self._provision("review-toggle", "operations", True)
        active = self._order(target, "reviewer_assigned")
        with self.assertRaises(ValidationError):
            target.with_user(self.admin)._trucalc_set_internal_reviewer(False)
        active._controlled_lifecycle_write({"status": "completed"})
        target.with_user(self.admin)._trucalc_set_internal_reviewer(False)
        self._assert_managed(target, "operations", False)
        self.assertEqual(self._audit_count(target, "reviewer_removed"), 1)
        self.assertEqual(active.reviewer_user_id, target)
        target.with_user(self.admin)._trucalc_set_internal_reviewer(True)
        self._assert_managed(target, "operations", True)
        self.assertEqual(self._audit_count(target, "reviewer_added"), 1)

    def test_deactivate_open_activity_acknowledgment_and_reactivation(self):
        target = self._provision("deactivate", "operations", False)
        order = self._order(self.reviewer, "completed")
        activity = self.env["mail.activity"].create({
            "activity_type_id": self.env.ref("mail.mail_activity_data_todo").id,
            "res_model_id": self.env["ir.model"]._get_id("trucalc.order"),
            "res_id": order.id,
            "user_id": target.id,
            "summary": "Transfer after deactivation",
        })
        with self.assertRaisesRegex(
            ValidationError, "user's open activities"
        ):
            target.with_user(self.admin)._trucalc_deactivate_internal_user()
        wizard = self.env["trucalc.internal.user.deactivate"].with_user(
            self.admin
        ).create({"target_user_id": target.id})
        with self.assertRaisesRegex(ValidationError, "open activities"):
            wizard.action_confirm()
        groups = target.group_ids
        companies = target.company_ids
        partner = target.partner_id
        target.with_user(self.admin)._trucalc_deactivate_internal_user(True)
        self._assert_managed(target, "operations", False, active=False)
        self.assertEqual(self._audit_count(target, "user_deactivated"), 1)
        self.assertEqual(target.group_ids, groups)
        self.assertEqual(target.company_ids, companies)
        self.assertEqual(target.partner_id, partner)
        self.assertTrue(activity.exists())

        new_bank = self.env["res.company"].with_context(
            trucalc_test_bank_fixture=True
        ).create({
            "name": "Internal Users Later Bank",
            "currency_id": self.main.currency_id.id,
            "trucalc_is_bank": True,
            "trucalc_bank_active": True,
        })
        self.assertNotIn(new_bank, target.company_ids)
        target.with_user(self.admin)._trucalc_reactivate_internal_user()
        self.assertEqual(self._audit_count(target, "user_reactivated"), 1)
        self.assertIn(new_bank, target.company_ids)
        self.assertEqual(
            target.company_ids,
            self.env["res.users"]._trucalc_internal_companies(),
        )
        self.assertEqual(
            target.action_id.id,
            self.env.ref("trucalc_orders.action_trucalc_orders").id,
        )

        ambiguous = self._provision("reactivate-ambiguous", "operations", False)
        ambiguous.active = False
        ambiguous.company_ids = [Command.set([
            self.main.id, self.unrelated.id,
        ])]
        with self.assertRaises(ValidationError):
            ambiguous.with_user(
                self.admin
            )._trucalc_reactivate_internal_user()

    def test_deactivation_guards_self_last_admin_and_active_review(self):
        with self.assertRaises(ValidationError):
            self.admin.with_user(self.admin)._trucalc_deactivate_internal_user()
        target = self._provision("deactivate-admin", "administrator", False)
        with patch.object(
            ResUsers, "_trucalc_active_internal_administrators",
            autospec=True, return_value=target,
        ):
            with self.assertRaises(ValidationError):
                target.with_user(self.admin)._trucalc_deactivate_internal_user()
        assigned = self._provision("deactivate-reviewer", "operations", True)
        self._order(assigned, "under_review")
        with self.assertRaises(ValidationError):
            assigned.with_user(self.admin)._trucalc_deactivate_internal_user()

    def test_invitation_send_resend_failure_and_authority(self):
        self.main.email = "trucalc-internal@example.test"
        target = self._provision("invite", "operations", False)

        def delivery(state):
            def send(mails, auto_commit=False, raise_exception=False,
                     post_send_callback=None):
                mails.write({
                    "state": state,
                    "failure_type": "mail_smtp" if state == "exception" else False,
                    "failure_reason": "Simulated" if state == "exception" else False,
                })
                return True
            return send

        with patch.object(
            MailMail, "send", autospec=True, side_effect=delivery("exception"),
        ):
            failed = target.with_user(
                self.admin
            )._trucalc_send_internal_invitation()
        self.assertFalse(failed["sent"])
        failed_mail = self.env["mail.mail"].sudo().browse(failed["mail_id"])
        self.assertEqual(failed_mail.state, "exception")
        self.assertFalse(failed_mail.auto_delete)
        self.assertIn("internal TruCalc account", failed_mail.body_html)
        self.assertNotIn("Bank portal", failed_mail.body_html)
        self.assertFalse(self.env["trucalc.internal.user.admin.audit"].sudo().search_count([
            ("target_user_id", "=", target.id),
            ("event_type", "in", ("invitation_sent", "invitation_resent")),
        ]))
        self.assertTrue(self.env["trucalc.internal.user.admin.audit"].sudo().search_count([
            ("target_user_id", "=", target.id),
            ("event_type", "=", "invitation_failed"),
        ]))

        with patch.object(
            MailMail, "send", autospec=True, side_effect=delivery("sent"),
        ):
            sent = target.with_user(
                self.admin
            )._trucalc_resend_internal_invitation()
        self.assertTrue(sent["sent"])
        self.assertTrue(self.env["trucalc.internal.user.admin.audit"].sudo().search_count([
            ("target_user_id", "=", target.id),
            ("event_type", "=", "invitation_resent"),
        ]))
        for actor in (self.ops, self.reviewer, self.portal, self.vendor_user):
            with self.assertRaises(AccessError):
                target.with_user(actor)._trucalc_send_internal_invitation()

    def test_confirmed_internal_invitation_rejected_and_reset_is_distinct(self):
        self.main.email = "trucalc-internal@example.test"
        target = self._provision("confirmed-reset", "operations", False)
        target.with_context(no_reset_password=True).write({"password": "Existing-Password"})
        target.with_user(target)._update_last_login()
        self.env.cr.execute("SELECT password FROM res_users WHERE id = %s", [target.id])
        password_hash = self.env.cr.fetchone()[0]
        with self.assertRaisesRegex(ValidationError, "never been invited"):
            target.with_user(self.admin)._trucalc_send_internal_invitation()

        def queued(mails, auto_commit=False, raise_exception=False,
                   post_send_callback=None):
            return True

        with patch.object(MailMail, "send", autospec=True, side_effect=queued):
            result = target.with_user(
                self.admin
            )._trucalc_send_internal_password_reset()
        self.assertTrue(result["queued"])
        self.assertFalse(result["sent"])
        mail = self.env["mail.mail"].sudo().browse(result["mail_id"])
        self.assertIn("Reset Your TruCalc Password", mail.body_html)
        self.assertIn("/web/reset_password", mail.body_html)
        self.env.cr.execute("SELECT password FROM res_users WHERE id = %s", [target.id])
        self.assertEqual(self.env.cr.fetchone()[0], password_hash)
        audit = self.env["trucalc.internal.user.admin.audit"].sudo().search([
            ("target_user_id", "=", target.id),
            ("event_type", "=", "password_reset_queued"),
        ])
        self.assertEqual(audit.mail_state, "outgoing")
        self.assertEqual(audit.metadata["delivery_outcome"], "queued")

    def test_expired_internal_invitation_is_resend_eligible(self):
        self.main.email = "trucalc-internal@example.test"
        target = self._provision("expired-invite", "operations", False)
        self.env["ir.config_parameter"].sudo().set_param(
            "auth_signup.signup.validity.hours", -1,
        )

        def sent(mails, auto_commit=False, raise_exception=False,
                 post_send_callback=None):
            mails.write({"state": "sent", "failure_type": False})
            return True

        with patch.object(MailMail, "send", autospec=True, side_effect=sent):
            target.with_user(self.admin)._trucalc_send_internal_invitation()
        self.assertEqual(
            target._trucalc_internal_account_state(), "invitation_expired",
        )
        with patch.object(MailMail, "send", autospec=True, side_effect=sent):
            result = target.with_user(
                self.admin
            )._trucalc_resend_internal_invitation()
        self.assertTrue(result["sent"])

    def test_audit_is_immutable_and_attribution_is_restricted(self):
        target = self._provision("audit", "operations", False)
        audit = self.env["trucalc.internal.user.admin.audit"].sudo().search([
            ("target_user_id", "=", target.id),
        ], limit=1)
        for method, argument in (
            ("write", {"metadata": {"changed": True}}),
            ("copy", None),
            ("unlink", None),
        ):
            with self.assertRaises(AccessError):
                getattr(audit, method)(argument) if argument is not None else getattr(
                    audit, method
                )()
        with self.assertRaises(AccessError):
            self.env["trucalc.internal.user.admin.audit"].with_user(
                self.admin
            ).create({
                "event_type": "user_provisioned",
                "event_at": fields.Datetime.now(),
                "actor_user_id": self.admin.id,
                "target_user_id": target.id,
                "normalized_identity": target.login,
            })

    def test_unified_manage_atomic_save_noop_and_history_preservation(self):
        target = self._provision(
            "unified-save", "operations", reviewer=True,
            phone="6155550100",
        )
        target.with_context(no_reset_password=True).write({
            "password": "Unified-A3-Existing-Password",
        })
        target.partner_id.sudo().signup_prepare(signup_type="signup:unified-a3")
        historical = self._order(target, "completed")
        user_id, partner_id, order_id = target.id, target.partner_id.id, historical.id
        companies = target.company_ids
        self.env.cr.execute("SELECT password FROM res_users WHERE id=%s", [target.id])
        password_hash = self.env.cr.fetchone()[0]
        mail_ids = self.env["mail.mail"].sudo().search([]).ids

        wizard = self._manage_wizard(target)
        self.assertEqual(wizard.phone, "(615) 555-0100")
        self.assertEqual(wizard.account_state, "invitation_pending")
        wizard.write({
            "name": "  Unified   Internal User  ",
            "login": "Unified.A3@Example.TEST",
            "phone": "+1 (615) 555-0198 extension 44",
            "role": "administrator",
            "reviewer": False,
            "acknowledge_account_link_invalidation": True,
        })
        fresh_action = wizard.action_save()
        target.invalidate_recordset()
        target.partner_id.invalidate_recordset()
        historical.invalidate_recordset()
        self.assertEqual(
            (target.id, target.partner_id.id, historical.id),
            (user_id, partner_id, order_id),
        )
        self.assertEqual(target.name, "Unified Internal User")
        self.assertEqual(target.login, "unified.a3@example.test")
        self.assertEqual(target.email, target.login)
        self.assertEqual(target.phone, "(615) 555-0198 ext. 44")
        self.assertFalse(target.partner_id.signup_type)
        self.assertEqual(target.company_ids, companies)
        self.assertEqual(historical.reviewer_user_id, target)
        self._assert_managed(target, "administrator", reviewer=False)
        self.env.cr.execute("SELECT password FROM res_users WHERE id=%s", [target.id])
        self.assertEqual(self.env.cr.fetchone()[0], password_hash)
        self.assertFalse(self.env["mail.mail"].sudo().search([
            ("id", "not in", mail_ids),
        ]))
        fresh_rows = self.env["trucalc.internal.user.management"].with_user(
            self.admin
        ).search(fresh_action["domain"])
        self.assertIn(target, fresh_rows.target_user_id)
        events = self.env["trucalc.internal.user.admin.audit"].sudo().search([
            ("target_user_id", "=", target.id),
        ]).mapped("event_type")
        self.assertIn("identity_updated", events)
        self.assertIn("email_updated", events)
        self.assertIn("role_changed", events)
        self.assertIn("reviewer_removed", events)

        noop = self._manage_wizard(target)
        audit_count = self.env[
            "trucalc.internal.user.admin.audit"
        ].sudo().search_count([("target_user_id", "=", target.id)])
        noop.action_save()
        self.assertEqual(self.env[
            "trucalc.internal.user.admin.audit"
        ].sudo().search_count([("target_user_id", "=", target.id)]), audit_count)

    def test_unified_manage_rolls_back_and_rejects_forgery(self):
        target = self._provision("unified-rollback", "operations", reviewer=True)
        collision = self._provision("unified-collision", "operations")
        active_review = self._order(target, "under_review")
        wizard = self._manage_wizard(target)
        original = (
            target.name, target.login, target.phone,
            target._trucalc_internal_role_key(),
            bool(target._trucalc_persona_membership()["reviewer"]),
        )
        wizard.write({
            "name": "Must Roll Back", "login": collision.login,
            "phone": "615-555-0111", "role": "administrator",
            "reviewer": False,
        })
        with self.assertRaises(ValidationError):
            wizard.action_save()
        target.invalidate_recordset()
        self.assertEqual((
            target.name, target.login, target.phone,
            target._trucalc_internal_role_key(),
            bool(target._trucalc_persona_membership()["reviewer"]),
        ), original)
        self.assertEqual(active_review.reviewer_user_id, target)
        self.assertFalse(self._audit_count(target, "identity_updated"))
        for values in (
            {"owner_user_id": self.ops.id},
            {"management_row_id": False},
            {"internal_company_id": self.unrelated.id},
            {"target_user_id": collision.id},
            {"user_active": False},
        ):
            with self.assertRaises(AccessError):
                wizard.write(values)
        for actor in (self.ops, self.reviewer, self.portal, self.vendor_user):
            with self.assertRaises(AccessError):
                wizard.with_user(actor).action_save()

    def test_unified_manage_guards_account_actions_and_internal_roster(self):
        self.main.email = "trucalc-internal@example.test"
        target = self._provision("unified-actions", "operations")
        wizard = self._manage_wizard(target)
        wizard.name = "Unsaved Name"
        with self.assertRaisesRegex(ValidationError, "Save or discard"):
            wizard.action_send_invitation()
        self.assertFalse(target.partner_id.signup_type)
        wizard.name = target.name

        def queued(mails, auto_commit=False, raise_exception=False,
                   post_send_callback=None):
            return True

        with patch.object(MailMail, "send", autospec=True, side_effect=queued):
            result = wizard.action_send_invitation()
        self.assertEqual(result["params"]["type"], "warning")
        target.invalidate_recordset()
        pending = self._manage_wizard(target)
        self.assertEqual(pending.account_state, "invitation_pending")
        with self.assertRaises(ValidationError):
            pending.action_send_invitation()
        pending.action_deactivate()
        target.invalidate_recordset()
        self.assertFalse(target.active)
        inactive = self._manage_wizard(target)
        self.assertEqual(inactive.account_state, "inactive")
        inactive.action_reactivate()
        target.invalidate_recordset()
        self.assertTrue(target.active)

        self_manage = self._manage_wizard(self.admin)
        self_manage.role = "operations"
        with self.assertRaisesRegex(ValidationError, "your own"):
            self_manage.action_save()
        self_manage = self._manage_wizard(self.admin)
        with self.assertRaisesRegex(ValidationError, "your own"):
            self_manage.action_deactivate()

    def test_ui_menu_order_surface_and_no_generic_user_acl(self):
        configuration = self.env.ref("trucalc_orders.menu_trucalc_configuration")
        self.assertEqual(
            [(menu.name, menu.sequence) for menu in configuration.child_id.sorted(
                lambda menu: (menu.sequence, menu.id)
            )],
            [
                ("Banks", 10), ("TruCalc Users", 20),
                ("Service Areas", 30),
                ("Negotiated Fee Schedules", 40),
                ("Document Tags", 50),
            ],
        )
        menu = self.env.ref("trucalc_orders.menu_trucalc_internal_users")
        self.assertEqual(menu.group_ids, self.roles["administrator"])
        action = self.env[
            "trucalc.internal.user.management"
        ].with_user(self.admin)._trucalc_open()
        self.assertEqual(action["res_model"], "trucalc.internal.user.management")
        self.assertEqual(action["view_mode"], "list")
        self.assertEqual(action["views"], [(
            self.env.ref(
                "trucalc_orders.view_trucalc_internal_user_management_list"
            ).id,
            "list",
        )])
        for actor in (self.ops, self.reviewer, self.portal, self.vendor_user):
            with self.assertRaises(AccessError):
                self.env[
                    "trucalc.internal.user.management"
                ].with_user(actor)._trucalc_open()
        self.assertFalse(
            self.env["res.users"].with_user(self.admin).has_access("write")
            and self.env["res.users"].with_user(self.admin).has_access("create")
        )
        for view_xmlid in (
            "view_trucalc_internal_user_provision_form",
            "view_trucalc_internal_user_management_form",
            "view_trucalc_internal_user_manage_form",
        ):
            arch = etree.fromstring(self.env.ref(
                "trucalc_orders.%s" % view_xmlid
            ).arch)
            self.assertFalse(set(arch.xpath(".//field/@name")) & {
                "password", "new_password", "group_ids", "company_id",
                "company_ids", "trucalc_bank_company_id", "trucalc_vendor_id",
            })
        list_arch = etree.fromstring(self.env.ref(
            "trucalc_orders.view_trucalc_internal_user_management_list"
        ).arch)
        self.assertEqual(len(list_arch.xpath(
            ".//field[@name='name'][@widget='trucalc_internal_user_manage_link']"
        )), 1)
        self.assertFalse(list_arch.xpath(".//button[@type='object']"))
        manage_arch = etree.fromstring(self.env.ref(
            "trucalc_orders.view_trucalc_internal_user_manage_form"
        ).arch)
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
        self.assertFalse(set(manage_arch.xpath(".//field/@name")) & {
            "password", "new_password", "group_ids", "company_id",
            "company_ids", "share", "trucalc_bank_company_id",
            "trucalc_vendor_id",
        })
