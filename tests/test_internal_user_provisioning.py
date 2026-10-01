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
            ("6625551212 ext 7", "6625551212 ext 7"),
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
            "trucalc.internal.user.management"
        ].with_user(self.admin).browse(action["res_id"])
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
            )._trucalc_send_internal_invitation()
        self.assertTrue(sent["sent"])
        self.assertTrue(self.env["trucalc.internal.user.admin.audit"].sudo().search_count([
            ("target_user_id", "=", target.id),
            ("event_type", "=", "invitation_resent"),
        ]))
        for actor in (self.ops, self.reviewer, self.portal, self.vendor_user):
            with self.assertRaises(AccessError):
                target.with_user(actor)._trucalc_send_internal_invitation()

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
        ):
            arch = etree.fromstring(self.env.ref(
                "trucalc_orders.%s" % view_xmlid
            ).arch)
            self.assertFalse(set(arch.xpath(".//field/@name")) & {
                "password", "new_password", "group_ids", "company_id",
                "company_ids", "trucalc_bank_company_id", "trucalc_vendor_id",
            })
