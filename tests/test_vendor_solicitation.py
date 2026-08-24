from datetime import timedelta
from lxml import etree

from odoo import Command, fields, tools
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "trucalc_vendor_solicitation")
class TestVendorSolicitation(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.groups = {
            name: cls.env.ref("trucalc_orders.%s" % name)
            for name in (
                "group_trucalc_admin", "group_trucalc_operations",
                "group_trucalc_reviewer", "group_bank_admin",
                "group_bank_requestor", "group_bank_view_only",
                "group_vendor_portal",
            )
        }
        cls.vendor_a = cls.env["trucalc.vendor"].create({
            "name": "4B2C1 Vendor A", "vendor_type": "appraiser",
        })
        cls.vendor_b = cls.env["trucalc.vendor"].create({
            "name": "4B2C1 Vendor B", "vendor_type": "appraiser",
        })
        cls.no_fee_vendor = cls.env["trucalc.vendor"].create({
            "name": "4B2C1 No Fee", "vendor_type": "appraiser",
        })
        cls.wrong_type_vendor = cls.env["trucalc.vendor"].create({
            "name": "4B2C1 Reviewer", "vendor_type": "reviewer",
        })
        cls.inactive_vendor = cls.env["trucalc.vendor"].create({
            "name": "4B2C1 Inactive", "vendor_type": "appraiser", "active": False,
        })
        cls.env["trucalc.vendor.fee"].create([
            {"vendor_id": cls.vendor_a.id, "service_type": "evaluation", "fee": 500},
            {"vendor_id": cls.vendor_b.id, "service_type": "evaluation", "fee": 600},
            {"vendor_id": cls.wrong_type_vendor.id,
             "service_type": "evaluation", "fee": 700},
            {"vendor_id": cls.inactive_vendor.id,
             "service_type": "evaluation", "fee": 800},
        ])
        cls.bank = cls.env["res.company"].create({"name": "4B2C1 Bank"})
        cls.admin = cls._user("4b2c1-admin", "group_trucalc_admin")
        cls.ops = cls._user("4b2c1-ops", "group_trucalc_operations")
        cls.env["res.lang"]._activate_lang("fr_FR")
        cls.admin.tz = "America/Chicago"
        cls.ops.lang = "fr_FR"
        cls.ops.tz = "Europe/Paris"
        cls.reviewer = cls._user("4b2c1-reviewer", "group_trucalc_reviewer")
        cls.bank_users = [
            cls._user("4b2c1-bank-admin", "group_bank_admin", bank=cls.bank),
            cls._user("4b2c1-bank-requestor", "group_bank_requestor", bank=cls.bank),
            cls._user("4b2c1-bank-view", "group_bank_view_only", bank=cls.bank),
        ]
        cls.vendor_user_a = cls._user(
            "4b2c1-vendor-a", "group_vendor_portal", vendor=cls.vendor_a
        )
        cls.vendor_user_b = cls._user(
            "4b2c1-vendor-b", "group_vendor_portal", vendor=cls.vendor_b
        )

    @classmethod
    def _user(cls, login, group_name, bank=False, vendor=False):
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": login, "login": login, "email": "%s@example.test" % login,
            "group_ids": [Command.set([cls.groups[group_name].id])],
            "trucalc_bank_company_id": bank.id if bank else False,
            "trucalc_vendor_id": vendor.id if vendor else False,
        })

    def _accepted_order(self, user=None):
        user = user or self.admin
        order = self.env["trucalc.order"].with_user(user).create({
            "borrower": "4B2C1 Conflict Borrower",
            "loan_number": "FORBIDDEN-LOAN-NUMBER",
            "loan_amount": 987654.32,
            "property_address": "101 Solicitation Way",
            "city": "Memphis", "state": "TN", "zip_code": "38103",
            "company_id": self.env.company.id,
            "service_type": "evaluation", "property_type": "single_family",
            "due_date": fields.Date.today() + timedelta(days=14),
        })
        order.action_accept_request()
        return order

    def _deadline(self, days=2):
        return fields.Datetime.now() + timedelta(days=days)

    def test_initial_solicitation_is_atomic_and_authoritative(self):
        self.assertTrue(
            getattr(self.env["trucalc.order"].action_bid_requested, "_api_private", False)
        )
        for manager in (self.admin, self.ops):
            order = self._accepted_order(manager)
            deadline = self._deadline()
            self.assertTrue(order.with_user(manager).action_request_vendor_bids(
                self.vendor_a, deadline
            ))
            self.assertEqual((order.status, order.bidding_round), ("bid_requested", 1))
            self.assertFalse(order.assigned_vendor_id)
            self.assertEqual(order.vendor_fee, 0)
            self.assertFalse(order.bid_ids)
            invitation = order.invitation_ids
            self.assertEqual(invitation.vendor_id, self.vendor_a)
            self.assertEqual(invitation.response_deadline, deadline)
            authorization = order.vendor_authorization_ids
            self.assertEqual(authorization.invitation_id, invitation)
            self.assertTrue(authorization.active)
            self.assertEqual(authorization.expires_at, deadline)
            self.assertTrue(order.message_ids.filtered(
                lambda message: "Bid requests sent" in (message.body or "")
            ))
            chatter = order.message_ids.filtered(
                lambda message: "Bid requests sent" in (message.body or "")
            )[:1].body
            self.assertIn(
                tools.format_datetime(
                    order.with_user(manager).env, deadline, dt_format="short"
                ),
                chatter,
            )
            self.assertNotIn(fields.Datetime.to_string(deadline), chatter)
            self.assertNotRegex(chatter, r"\b\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\b")
            self.assertTrue(self.env["trucalc.bid.audit"].search([
                ("order_id", "=", order.id),
                ("action", "=", "solicitation_created"),
            ]))

    def test_roles_state_deadline_and_eligibility_are_revalidated(self):
        denied = [self.reviewer, *self.bank_users,
                  self.vendor_user_a, self.vendor_user_b]
        for user in denied:
            with self.assertRaises(AccessError):
                self._accepted_order().with_user(user).action_request_vendor_bids(
                    self.vendor_a, self._deadline()
                )
        invalid_vendors = (
            self.inactive_vendor, self.wrong_type_vendor, self.no_fee_vendor
        )
        for vendor in invalid_vendors:
            order = self._accepted_order()
            with self.assertRaises(ValidationError):
                order.action_request_vendor_bids(vendor, self._deadline())
            self.assertEqual((order.status, order.bidding_round), ("accepted", 0))
            self.assertFalse(order.invitation_ids)
            self.assertFalse(self.env[
                "trucalc.order.vendor.authorization"
            ].sudo().search([("order_id", "=", order.id)]))
        for deadline in (False, fields.Datetime.now() - timedelta(seconds=1)):
            order = self._accepted_order()
            with self.assertRaises(ValidationError):
                order.action_request_vendor_bids(self.vendor_a, deadline)
            self.assertEqual(order.status, "accepted")
        with self.assertRaises(ValidationError):
            self._accepted_order().action_request_vendor_bids(
                self.env["trucalc.vendor"].browse(), self._deadline()
            )

    def test_manage_adds_only_new_vendor_with_common_deadline(self):
        order = self._accepted_order()
        deadline = self._deadline()
        order.action_request_vendor_bids(self.vendor_a, deadline)
        invitation_a = order.invitation_ids
        order.with_user(self.ops).action_add_vendor_bid_requests(self.vendor_b)
        invitations = order.invitation_ids.sorted("vendor_id")
        self.assertEqual(len(invitations), 2)
        self.assertEqual(set(invitations.mapped("response_deadline")), {deadline})
        self.assertEqual(invitation_a, order.invitation_ids.filtered(
            lambda invitation: invitation.vendor_id == self.vendor_a
        ))
        self.assertEqual(order.status, "bid_requested")
        self.assertEqual(order.bidding_round, 1)
        self.assertFalse(order.bid_ids)
        self.assertFalse(order.assigned_vendor_id)
        self.assertEqual(order.vendor_fee, 0)
        with self.assertRaises(ValidationError):
            order.action_add_vendor_bid_requests(self.vendor_a)

        projection = self.env["trucalc.vendor.order"]
        self.assertTrue(projection.with_user(self.vendor_user_a).search([
            ("order_number", "=", order.order_number)
        ]))
        self.assertTrue(projection.with_user(self.vendor_user_b).search([
            ("order_number", "=", order.order_number)
        ]))

    def test_expired_round_blocks_add_until_deadline_extension(self):
        order = self._accepted_order()
        order.action_request_vendor_bids(self.vendor_a, self._deadline())
        invitation = order.invitation_ids
        invitation._controlled_write({
            "response_deadline": fields.Datetime.now() - timedelta(seconds=1)
        })
        self.env["trucalc.order.vendor.authorization"]._update_invitation_expiry(
            invitation
        )
        with self.assertRaises(ValidationError):
            order.action_add_vendor_bid_requests(self.vendor_b)
        extended = self._deadline(3)
        order.action_extend_bid_deadline(extended)
        order.action_add_vendor_bid_requests(self.vendor_b)
        self.assertEqual(set(order.invitation_ids.mapped("response_deadline")), {extended})

    def test_extend_deadline_updates_all_authorizations_without_side_effects(self):
        order = self._accepted_order()
        old = self._deadline()
        order.action_request_vendor_bids(self.vendor_a | self.vendor_b, old)
        invitation_ids = order.invitation_ids.ids
        authorization_ids = order.vendor_authorization_ids.ids
        new = self._deadline(4)
        order.with_user(self.ops).action_extend_bid_deadline(new)
        self.assertEqual(set(order.invitation_ids.mapped("response_deadline")), {new})
        self.assertEqual(set(order.vendor_authorization_ids.mapped("expires_at")), {new})
        self.assertEqual(order.invitation_ids.ids, invitation_ids)
        self.assertEqual(order.vendor_authorization_ids.ids, authorization_ids)
        self.assertEqual((order.status, order.bidding_round), ("bid_requested", 1))
        self.assertFalse(order.bid_ids)
        self.assertFalse(order.assigned_vendor_id)
        self.assertEqual(order.vendor_fee, 0)
        with self.assertRaises(ValidationError):
            order.action_extend_bid_deadline(old)
        self.assertTrue(self.env["trucalc.bid.audit"].search([
            ("order_id", "=", order.id), ("action", "=", "deadline_extended")
        ]))
        chatter = order.message_ids.filtered(
            lambda message: "Bid response deadline extended" in (message.body or "")
        )[:1].body
        ops_env = order.with_user(self.ops).env
        old_local = tools.format_datetime(ops_env, old, dt_format="short")
        new_local = tools.format_datetime(ops_env, new, dt_format="short")
        self.assertIn(old_local, chatter)
        self.assertIn(new_local, chatter)
        self.assertNotIn(fields.Datetime.to_string(new), chatter)
        self.assertNotRegex(chatter, r"\b\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\b")
        self.assertNotEqual(
            new_local,
            tools.format_datetime(ops_env, new, tz="UTC", dt_format="short"),
        )
        projection = self.env["trucalc.vendor.order"].with_user(
            self.vendor_user_a
        ).search([("order_number", "=", order.order_number)])
        self.assertEqual(projection.response_deadline, new)

    def test_wizard_inventory_and_acl(self):
        order = self._accepted_order()
        action = order.action_open_request_bids_wizard()
        wizard = self.env[action["res_model"]].with_user(self.admin).with_context(
            action["context"]
        ).create({})
        self.assertEqual(wizard.mode, "request")
        displayed = wizard.line_ids.mapped("vendor_id")
        self.assertTrue(self.vendor_a in displayed and self.vendor_b in displayed)
        self.assertNotIn(self.no_fee_vendor, displayed)
        self.assertNotIn(self.wrong_type_vendor, displayed)
        self.assertNotIn(self.inactive_vendor, displayed)
        fees = {line.vendor_id: line.standard_fee for line in wizard.line_ids}
        self.assertEqual((fees[self.vendor_a], fees[self.vendor_b]), (500, 600))
        line_a = wizard.line_ids.filtered(lambda line: line.vendor_id == self.vendor_a)
        line_b = wizard.line_ids.filtered(lambda line: line.vendor_id == self.vendor_b)
        line_a.selected = True
        self.assertEqual(wizard.line_ids.filtered("selected").mapped("vendor_id"), self.vendor_a)
        line_b.selected = True
        self.assertEqual(
            set(wizard.line_ids.filtered("selected").mapped("vendor_id")),
            {self.vendor_a, self.vendor_b},
        )
        line_a.selected = False
        self.assertEqual(wizard.line_ids.filtered("selected").mapped("vendor_id"), self.vendor_b)
        for user in (self.reviewer, *self.bank_users,
                     self.vendor_user_a, self.vendor_user_b):
            self.assertFalse(self.env["trucalc.bid.request.wizard"].with_user(
                user
            ).has_access("create"))

    def test_wizard_selection_view_is_inline_and_does_not_open_vendor(self):
        view = self.env.ref("trucalc_orders.view_trucalc_bid_request_wizard_form")
        arch = etree.fromstring(view.arch.encode())
        vendor_list = arch.xpath("//field[@name='line_ids']/list")[0]
        self.assertEqual(vendor_list.get("editable"), "bottom")
        self.assertEqual(vendor_list.get("create"), "0")
        selected = vendor_list.xpath("./field[@name='selected']")[0]
        self.assertEqual(selected.get("widget"), "boolean_toggle")
        vendor = vendor_list.xpath("./field[@name='vendor_id']")[0]
        self.assertEqual(vendor.get("force_save"), "1")
        self.assertIn("'no_open': True", vendor.get("options"))

        deadline = arch.xpath("//field[@name='response_deadline']")[0]
        self.assertEqual(deadline.get("widget"), "datetime")
        self.assertIn("'show_time': True", deadline.get("options"))
        self.assertIn("'rounding': 15", deadline.get("options"))

        extension_view = self.env.ref(
            "trucalc_orders.view_trucalc_bid_deadline_wizard_form"
        )
        extension_arch = etree.fromstring(extension_view.arch.encode())
        for name in ("current_deadline", "new_deadline"):
            field = extension_arch.xpath("//field[@name='%s']" % name)[0]
            self.assertEqual(field.get("widget"), "datetime")
            self.assertIn("'show_time': True", field.get("options"))

        order_view = self.env.ref("trucalc_orders.view_trucalc_order_form")
        order_arch = etree.fromstring(order_view.arch.encode())
        due_date = order_arch.xpath("//field[@name='due_date']")[0]
        self.assertIn("'numeric': True", due_date.get("options"))

    def test_empty_wizard_selection_is_controlled_and_non_mutating(self):
        order = self._accepted_order()
        action = order.action_open_request_bids_wizard()
        wizard = self.env[action["res_model"]].with_user(self.admin).with_context(
            action["context"]
        ).create({"response_deadline": self._deadline()})
        before_messages = order.message_ids.ids
        before_audits = self.env["trucalc.bid.audit"].search([
            ("order_id", "=", order.id)
        ]).ids
        with self.assertRaisesRegex(
            ValidationError, "Select at least one vendor before requesting bids"
        ):
            wizard.action_confirm()
        self.assertEqual((order.status, order.bidding_round), ("accepted", 0))
        self.assertFalse(order.invitation_ids)
        self.assertFalse(self.env[
            "trucalc.order.vendor.authorization"
        ].sudo().search([("order_id", "=", order.id)]))
        self.assertFalse(order.bid_ids)
        self.assertFalse(order.assigned_vendor_id)
        self.assertEqual(order.vendor_fee, 0)
        self.assertEqual(order.message_ids.ids, before_messages)
        self.assertEqual(self.env["trucalc.bid.audit"].search([
            ("order_id", "=", order.id)
        ]).ids, before_audits)

    def test_wizard_confirmation_revalidates_selection_and_manage_uses_same_lines(self):
        order = self._accepted_order()
        action = order.action_open_request_bids_wizard()
        wizard = self.env[action["res_model"]].with_user(self.admin).with_context(
            action["context"]
        ).create({"response_deadline": self._deadline()})
        line_a = wizard.line_ids.filtered(lambda line: line.vendor_id == self.vendor_a)
        line_a.selected = True
        line_a.vendor_id = self.no_fee_vendor
        with self.assertRaises(ValidationError):
            wizard.action_confirm()
        self.assertEqual(order.status, "accepted")
        self.assertFalse(order.invitation_ids)

        line_a.vendor_id = self.vendor_a
        wizard.action_confirm()
        manage_action = order.action_open_manage_bid_requests_wizard()
        manage = self.env[manage_action["res_model"]].with_user(self.admin).with_context(
            manage_action["context"]
        ).create({})
        invited = manage.line_ids.filtered("already_invited")
        self.assertEqual(invited.mapped("vendor_id"), self.vendor_a)
        self.assertFalse(invited.selected)
        available_b = manage.line_ids.filtered(lambda line: line.vendor_id == self.vendor_b)
        available_b.selected = True
        manage.action_confirm()
        self.assertEqual(set(order.invitation_ids.mapped("vendor_id")), {
            self.vendor_a, self.vendor_b,
        })
        chatter = order.message_ids.filtered(
            lambda message: "Additional bid requests sent" in (message.body or "")
        )[:1].body
        deadline = order._current_round_deadline()
        self.assertIn(
            tools.format_datetime(
                order.with_user(self.admin).env, deadline, dt_format="short"
            ),
            chatter,
        )
        self.assertNotIn(fields.Datetime.to_string(deadline), chatter)
        self.assertNotRegex(chatter, r"\b\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\b")

    def test_projection_disclosure_and_isolation(self):
        order = self._accepted_order()
        deadline = self._deadline()
        self.assertFalse(self.env["trucalc.vendor.order"].with_user(
            self.vendor_user_a
        ).search([("order_number", "=", order.order_number)]))
        order.action_request_vendor_bids(self.vendor_a, deadline)
        projection = self.env["trucalc.vendor.order"].with_user(
            self.vendor_user_a
        ).search([("order_number", "=", order.order_number)])
        self.assertEqual(projection.borrower, order.borrower)
        self.assertEqual(projection.due_date, order.due_date)
        self.assertEqual(projection.response_deadline, deadline)
        public_fields = projection.fields_get()
        for prohibited in (
            "loan_amount", "loan_number", "notes", "review_fee", "decline_reason",
            "requestor_id", "document_ids", "bid_ids", "invitation_ids",
            "message_ids", "activity_ids",
        ):
            self.assertNotIn(prohibited, public_fields)
        self.assertFalse(self.env["trucalc.vendor.order"].with_user(
            self.vendor_user_b
        ).search([("order_number", "=", order.order_number)]))
        with self.assertRaises(AccessError):
            self.env["trucalc.order"].with_user(self.vendor_user_a).check_access("read")
        with self.assertRaises(AccessError):
            self.env["trucalc.document"].with_user(self.vendor_user_a).check_access("read")
