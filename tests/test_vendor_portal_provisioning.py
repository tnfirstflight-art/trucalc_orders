from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "trucalc_vendor_portal_provisioning")
class TestVendorPortalProvisioning(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.groups = {
            name: cls.env.ref("trucalc_orders.%s" % name)
            for name in (
                "group_trucalc_admin",
                "group_trucalc_operations",
                "group_trucalc_reviewer",
                "group_bank_admin",
                "group_vendor_portal",
            )
        }
        cls.portal_group = cls.env.ref("base.group_portal")
        cls.internal_group = cls.env.ref("base.group_user")
        cls.vendor = cls.env["trucalc.vendor"].create({
            "name": "4B2B1 Vendor", "vendor_type": "appraiser",
        })
        cls.other_vendor = cls.env["trucalc.vendor"].create({
            "name": "4B2B1 Other Vendor", "vendor_type": "environmental",
        })
        cls.env["trucalc.vendor.fee"].create({
            "vendor_id": cls.vendor.id, "service_type": "evaluation", "fee": 500,
        })
        cls.bank = cls.env["res.company"].create({"name": "4B2B1 Bank"})
        cls.admin = cls._user("admin", cls.groups["group_trucalc_admin"])
        cls.ops = cls._user("ops", cls.groups["group_trucalc_operations"])
        cls.reviewer = cls._user("reviewer", cls.groups["group_trucalc_reviewer"])
        cls.bank_user = cls._user(
            "bank", cls.groups["group_bank_admin"], bank=cls.bank
        )
        cls.vendor_user = cls._user(
            "vendor-existing", cls.groups["group_vendor_portal"], vendor=cls.other_vendor
        )

    @classmethod
    def _user(cls, suffix, group, bank=False, vendor=False, active=True):
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "4B2B1 %s" % suffix,
            "login": "4b2b1-%s" % suffix,
            "email": "4b2b1-%s@example.test" % suffix,
            "active": active,
            "group_ids": [Command.set([group.id])],
            "trucalc_bank_company_id": bank.id if bank else False,
            "trucalc_vendor_id": vendor.id if vendor else False,
        })

    @classmethod
    def _portal_user(cls, suffix, active=True, invited=False):
        user = cls._user(suffix, cls.portal_group, active=active)
        if invited:
            user.partner_id.signup_type = "signup"
        return user

    def _wizard(self, user=False, vendor=None):
        values = {"vendor_id": (vendor or self.vendor).id}
        if user:
            values["user_id"] = user.id
        return self.env["trucalc.vendor.portal.provision"].with_user(
            self.admin
        ).create(values)

    def _assert_unchanged(self, user, groups, vendor=False, bank=False):
        user.invalidate_recordset()
        self.assertEqual(set(user.group_ids.ids), set(groups.ids))
        self.assertEqual(user.trucalc_vendor_id.id, vendor.id if vendor else False)
        self.assertEqual(
            user.trucalc_bank_company_id.id, bank.id if bank else False
        )

    def test_admin_provisions_confirmed_and_invited_portal_users(self):
        for suffix, invited in (("confirmed", False), ("invited", True)):
            user = self._portal_user(suffix, invited=invited)
            self._wizard(user).action_provision()
            self.assertEqual(user.trucalc_vendor_id, self.vendor)
            self.assertTrue(user.has_group("trucalc_orders.group_vendor_portal"))
            self.assertTrue(user.has_group("base.group_portal"))
            self.assertFalse(user.has_group("base.group_user"))
            self.assertTrue(user.share)
            self.assertFalse(user.trucalc_bank_company_id)

    def test_create_and_invite_from_unique_vendor_email(self):
        self.vendor.email = "New.Vendor@Example.Test"
        before = {
            name: self.env[name].sudo().search_count([])
            for name in (
                "trucalc.order.vendor.authorization",
                "trucalc.bid.invitation",
                "trucalc.bid",
            )
        }
        PortalLine = type(self.env["portal.wizard.user"])
        with patch.object(PortalLine, "_send_email", autospec=True, return_value=True) as send:
            self._wizard().action_create_and_invite()

        user = self.env["res.users"].sudo().search([
            ("login", "=", "new.vendor@example.test"),
        ])
        self.assertEqual(len(user), 1)
        self.assertEqual(user.partner_id.name, self.vendor.name)
        self.assertEqual(user.partner_id.email, "new.vendor@example.test")
        self.assertEqual(user.trucalc_vendor_id, self.vendor)
        self.assertTrue(user.active)
        self.assertTrue(user.share)
        self.assertTrue(user.has_group("base.group_portal"))
        self.assertTrue(user.has_group("trucalc_orders.group_vendor_portal"))
        self.assertFalse(user.has_group("base.group_user"))
        self.assertEqual(user.partner_id.signup_type, "signup")
        send.assert_called_once()
        self.assertEqual(
            {
                name: self.env[name].sudo().search_count([])
                for name in before
            },
            before,
        )

    def test_create_and_invite_reuses_existing_contact_without_user(self):
        self.vendor.email = "contact-only@example.test"
        partner = self.env["res.partner"].create({
            "name": "Existing Vendor Contact",
            "email": self.vendor.email,
        })
        PortalLine = type(self.env["portal.wizard.user"])
        with patch.object(PortalLine, "_send_email", autospec=True, return_value=True):
            self._wizard().action_create_and_invite()
        users = partner.with_context(active_test=False).user_ids
        self.assertEqual(len(users), 1)
        self.assertEqual(users.trucalc_vendor_id, self.vendor)

    def test_create_path_reuses_eligible_plain_portal_user(self):
        user = self._portal_user("email-reuse")
        self.vendor.email = user.email
        self._wizard().action_create_and_invite()
        self.assertEqual(user.trucalc_vendor_id, self.vendor)
        self.assertTrue(user.has_group("trucalc_orders.group_vendor_portal"))
        self.assertEqual(
            self.env["res.users"].sudo().search_count([("login", "=", user.login)]),
            1,
        )

    def test_create_path_requires_one_valid_vendor_email(self):
        for email in (False, "not-an-email", "one@example.test, two@example.test"):
            self.vendor.email = email
            with self.assertRaises(ValidationError):
                self._wizard().action_create_and_invite()

    def test_create_path_rejects_ambiguous_contacts_and_users(self):
        email = "ambiguous@example.test"
        self.vendor.email = email
        self.env["res.partner"].create([
            {"name": "Ambiguous One", "email": email},
            {"name": "Ambiguous Two", "email": email.upper()},
        ])
        with self.assertRaises(ValidationError):
            self._wizard().action_create_and_invite()

        user_email = "ambiguous-users@example.test"
        self.vendor.email = user_email
        first = self._portal_user("ambiguous-user-one")
        second = self._portal_user("ambiguous-user-two")
        first.partner_id.email = user_email
        second.partner_id.email = user_email.upper()
        with self.assertRaises(ValidationError):
            self._wizard().action_create_and_invite()

    def test_create_path_rejects_incompatible_identity_collisions(self):
        for user in (
            self._user("create-internal", self.internal_group),
            self._user("create-trucalc-internal", self.groups["group_trucalc_operations"]),
            self.bank_user,
            self.vendor_user,
        ):
            self.vendor.email = user.login
            original_groups = user.group_ids
            original_vendor = user.trucalc_vendor_id
            original_bank = user.trucalc_bank_company_id
            with self.assertRaises(ValidationError):
                self._wizard().action_create_and_invite()
            self._assert_unchanged(
                user,
                original_groups,
                vendor=original_vendor,
                bank=original_bank,
            )

    def test_create_path_is_atomic_when_trucalc_mapping_fails(self):
        self.vendor.email = "atomic@example.test"
        partner_count = self.env["res.partner"].sudo().search_count([])
        user_count = self.env["res.users"].sudo().with_context(active_test=False).search_count([])
        Users = type(self.env["res.users"])
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():
                with patch.object(
                    Users,
                    "_trucalc_provision_vendor_portal",
                    autospec=True,
                    side_effect=ValidationError("forced mapping failure"),
                ):
                    self._wizard().action_create_and_invite()
        self.assertEqual(self.env["res.partner"].sudo().search_count([]), partner_count)
        self.assertEqual(
            self.env["res.users"].sudo().with_context(active_test=False).search_count([]),
            user_count,
        )

    def test_all_active_vendor_types_are_eligible(self):
        for vendor_type in ("appraiser", "reviewer", "environmental"):
            vendor = self.env["trucalc.vendor"].create({
                "name": "4B2B1 %s" % vendor_type,
                "vendor_type": vendor_type,
            })
            user = self._portal_user("type-%s" % vendor_type)
            self._wizard(user, vendor).action_provision()
            self.assertEqual(user.trucalc_vendor_id, vendor)

    def test_only_trucalc_administrator_can_invoke_public_action(self):
        unauthorized = (
            self.ops,
            self.reviewer,
            self.bank_user,
            self.vendor_user,
            self._portal_user("plain-rpc"),
        )
        for index, actor in enumerate(unauthorized):
            target = self._portal_user("denied-%s" % index)
            wizard = self._wizard(target)
            with self.assertRaises(AccessError):
                wizard.with_user(actor).action_provision()
            self._assert_unchanged(target, self.portal_group)

        self.vendor.email = "unauthorized-create@example.test"
        wizard = self._wizard()
        for actor in unauthorized:
            with self.assertRaises(AccessError):
                wizard.with_user(actor).action_create_and_invite()

    def test_only_trucalc_administrator_has_wizard_model_access(self):
        for actor in (
            self.ops,
            self.reviewer,
            self.bank_user,
            self.vendor_user,
            self._portal_user("plain-wizard-acl"),
        ):
            with self.assertRaises(AccessError):
                self.env["trucalc.vendor.portal.provision"].with_user(actor).create({
                    "user_id": self._portal_user("acl-target-%s" % actor.id).id,
                    "vendor_id": self.vendor.id,
                })

    def test_internal_and_non_portal_users_are_rejected(self):
        for target in (
            self._user("internal-target", self.internal_group),
            self._user("operations-target", self.groups["group_trucalc_operations"]),
        ):
            with self.assertRaises(ValidationError):
                self._wizard(target).action_provision()
            self.assertFalse(target.trucalc_vendor_id)

    def test_bank_persona_and_bank_mapping_are_rejected(self):
        mapped_portal = self._portal_user("bank-mapped")
        mapped_portal.sudo().trucalc_bank_company_id = self.bank
        for target in (self.bank_user, mapped_portal):
            original_groups = target.group_ids
            with self.assertRaises(ValidationError):
                self._wizard(target).action_provision()
            self._assert_unchanged(
                target, original_groups, bank=target.trucalc_bank_company_id
            )

    def test_existing_vendor_persona_mapping_and_reassignment_are_rejected(self):
        mapped_portal = self._portal_user("vendor-mapped")
        mapped_portal.sudo().trucalc_vendor_id = self.other_vendor
        for target in (self.vendor_user, mapped_portal):
            original_groups = target.group_ids
            original_vendor = target.trucalc_vendor_id
            with self.assertRaises(ValidationError):
                self._wizard(target).action_provision()
            self._assert_unchanged(target, original_groups, vendor=original_vendor)

    def test_inactive_user_vendor_and_missing_records_fail_without_partial_state(self):
        inactive_user = self._portal_user("inactive-user")
        inactive_user.active = False
        inactive_vendor = self.env["trucalc.vendor"].create({
            "name": "4B2B1 Inactive", "vendor_type": "appraiser", "active": False,
        })
        target = self._portal_user("inactive-vendor-target")
        for user, vendor in ((inactive_user, self.vendor), (target, inactive_vendor)):
            original_groups = user.group_ids
            with self.assertRaises(ValidationError):
                self._wizard(user, vendor).action_provision()
            self._assert_unchanged(user, original_groups)

    def test_portal_users_cannot_mutate_mapping_or_groups_directly(self):
        user = self._portal_user("self-service")
        with self.assertRaises(AccessError):
            user.with_user(user).write({"trucalc_vendor_id": self.vendor.id})
        with self.assertRaises(AccessError):
            user.with_user(user).write({
                "group_ids": [Command.link(self.groups["group_vendor_portal"].id)]
            })
        self._assert_unchanged(user, self.portal_group)

    def test_provisioning_does_not_create_security_domain_records(self):
        target = self._portal_user("non-interaction")
        models = (
            "trucalc.order.vendor.authorization",
            "trucalc.bid.invitation",
            "trucalc.bid",
        )
        before = {name: self.env[name].sudo().search_count([]) for name in models}
        self._wizard(target).action_provision()
        after = {name: self.env[name].sudo().search_count([]) for name in models}
        self.assertEqual(after, before)
        with self.assertRaises(AccessError):
            self.env["trucalc.order"].with_user(target).check_access("read")
        with self.assertRaises(AccessError):
            self.env["trucalc.document"].with_user(target).check_access("read")
        with self.assertRaises(AccessError):
            self.env["ir.attachment"].with_user(target).check_access("read")
        self.assertFalse(
            self.env["trucalc.vendor.order"].with_user(target).search([])
        )

    def test_preexisting_authorization_drives_projection_after_provisioning(self):
        target = self._portal_user("preauthorized")
        order = self.env["trucalc.order"].with_user(self.admin).create({
            "borrower": "4B2B1 Protected Borrower",
            "property_address": "1 Provisioning Way",
            "company_id": self.env.company.id,
            "service_type": "evaluation",
        })
        order.with_user(self.admin).action_accept_request()
        order.with_user(self.admin).action_bid_requested()
        invitation = self.env["trucalc.bid.invitation"].with_user(self.admin).create({
            "order_id": order.id,
            "vendor_id": self.vendor.id,
            "response_deadline": fields.Datetime.add(fields.Datetime.now(), days=1),
        })
        authorization = self.env["trucalc.order.vendor.authorization"].sudo().search([
            ("invitation_id", "=", invitation.id),
        ])
        counts = {
            name: self.env[name].sudo().search_count([])
            for name in (
                "trucalc.order.vendor.authorization",
                "trucalc.bid.invitation",
                "trucalc.bid",
            )
        }
        self._wizard(target).action_provision()
        self.assertEqual(
            self.env["trucalc.vendor.order"].with_user(target).search([]).order_number,
            order.order_number,
        )
        self.assertTrue(authorization.active)
        self.assertEqual(
            {
                name: self.env[name].sudo().search_count([])
                for name in counts
            },
            counts,
        )
        other = self.env["trucalc.order.vendor.authorization"].sudo().search([
            ("vendor_id", "=", self.other_vendor.id), ("active", "=", True),
        ])
        self.assertFalse(
            set(other.mapped("order_id.order_number"))
            & set(self.env["trucalc.vendor.order"].with_user(target).search([]).mapped("order_number"))
        )
