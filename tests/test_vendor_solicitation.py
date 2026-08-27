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

    def _reopened_unsolicited_order(self):
        order = self._accepted_order()
        prior_deadline = self._deadline()
        order.action_request_vendor_bids(self.vendor_a, prior_deadline)
        prior_invitation = order.invitation_ids
        bid = prior_invitation.with_user(
            self.vendor_user_a
        ).action_vendor_submit_response("standard_terms_accepted")
        bid.with_user(self.admin)._action_confirm_engagement()
        prior_engagement = order.sudo().engagement_ids.filtered("active")
        order.with_user(self.ops).action_reopen_bidding()
        return order, prior_invitation, bid, prior_engagement, prior_deadline

    def test_reopened_round_is_initialized_by_request_bids_once(self):
        order, prior_invitation, prior_bid, prior_engagement, prior_deadline = (
            self._reopened_unsolicited_order()
        )
        prior_authorization_ids = self.env[
            "trucalc.order.vendor.authorization"
        ].sudo().with_context(active_test=False).search([
            ("order_id", "=", order.id),
            ("round_number", "=", 1),
        ]).ids
        prior_audit_ids = self.env["trucalc.bid.audit"].search([
            ("order_id", "=", order.id),
        ]).ids
        prior_event_ids = prior_engagement.event_ids.ids

        self.assertEqual((order.status, order.bidding_round), ("bid_requested", 2))
        self.assertFalse(order._current_round_invitations())
        self.assertFalse(order.current_round_has_solicitation)
        self.assertTrue(order.can_request_vendor_bids)
        self.assertEqual(prior_invitation.response_deadline, prior_deadline)
        self.assertEqual(prior_invitation.round_number, 1)
        self.assertEqual(prior_invitation.state, "closed")
        self.assertFalse(prior_engagement.active)
        with self.assertRaisesRegex(ValidationError, "must be solicited before managing"):
            order.action_open_manage_bid_requests_wizard()
        with self.assertRaisesRegex(ValidationError, "must be solicited before extending"):
            order.action_open_extend_bid_deadline_wizard()
        with self.assertRaisesRegex(ValidationError, "no single response deadline"):
            order.action_add_vendor_bid_requests(self.vendor_b)
        with self.assertRaisesRegex(ValidationError, "no single response deadline"):
            order.action_extend_bid_deadline(self._deadline(3))

        action = order.action_open_request_bids_wizard()
        self.assertEqual(action["name"], "Request Bids")
        wizard = self.env[action["res_model"]].with_user(self.ops).with_context(
            action["context"]
        ).create({})
        self.assertEqual(wizard.mode, "request")
        self.assertFalse(wizard.response_deadline)
        line_b = wizard.line_ids.filtered(lambda line: line.vendor_id == self.vendor_b)
        line_b.selected = True
        with self.assertRaises(ValidationError):
            wizard.action_confirm()
        self.assertFalse(order._current_round_invitations())
        self.assertEqual((order.status, order.bidding_round), ("bid_requested", 2))

        with self.assertRaises(ValidationError):
            order.action_request_vendor_bids(self.no_fee_vendor, self._deadline(3))
        self.assertFalse(order._current_round_invitations())
        new_deadline = self._deadline(4)
        wizard.response_deadline = new_deadline
        wizard.action_confirm()
        current = order._current_round_invitations()
        self.assertEqual(len(current), 1)
        self.assertEqual(current.vendor_id, self.vendor_b)
        self.assertEqual(current.round_number, 2)
        self.assertEqual(current.response_deadline, new_deadline)
        self.assertEqual(order.bidding_round, 2)
        self.assertTrue(order.current_round_has_solicitation)
        self.assertFalse(order.can_request_vendor_bids)
        current_authorizations = order.sudo().vendor_authorization_ids.filtered(
            lambda authorization: authorization.round_number == 2
        )
        self.assertEqual(len(current_authorizations), 1)
        self.assertEqual(current_authorizations.vendor_id, self.vendor_b)
        self.assertEqual(current_authorizations.invitation_id, current)
        self.assertTrue(current_authorizations.active)
        self.assertEqual(order._current_round_deadline(), new_deadline)
        self.assertEqual(prior_invitation.response_deadline, prior_deadline)
        self.assertEqual((prior_bid.round_number, prior_bid.status), (1, "selected"))
        self.assertEqual(prior_engagement.event_ids.ids, prior_event_ids)
        all_authorization_ids = self.env[
            "trucalc.order.vendor.authorization"
        ].sudo().with_context(active_test=False).search([
            ("order_id", "=", order.id),
        ]).ids
        self.assertTrue(set(prior_authorization_ids).issubset(
            set(all_authorization_ids)
        ))
        self.assertEqual(
            self.env["trucalc.bid.audit"].search([
                ("order_id", "=", order.id),
                ("id", "in", prior_audit_ids),
            ]).ids,
            prior_audit_ids,
        )
        self.assertEqual(
            order.action_open_manage_bid_requests_wizard()["name"],
            "Manage Bid Requests",
        )
        self.assertEqual(
            order.action_open_extend_bid_deadline_wizard()["name"],
            "Extend Bid Deadline",
        )
        with self.assertRaises(ValidationError):
            order.action_open_request_bids_wizard()
        with self.assertRaises(ValidationError):
            order.action_request_vendor_bids(self.vendor_a, self._deadline(5))

        order_arch = etree.fromstring(self.env.ref(
            "trucalc_orders.view_trucalc_order_form"
        ).arch_db.encode())
        self.assertEqual(order_arch.xpath(
            "//button[@name='action_open_request_bids_wizard']"
        )[0].get("invisible"), "not can_request_vendor_bids")
        for button_name in (
            "action_open_manage_bid_requests_wizard",
            "action_open_extend_bid_deadline_wizard",
        ):
            self.assertIn(
                "current_round_has_solicitation",
                order_arch.xpath("//button[@name='%s']" % button_name)[0].get(
                    "invisible"
                ),
            )

        round_two_bid = current.with_user(
            self.vendor_user_b
        ).action_vendor_submit_response("standard_terms_accepted")
        round_two_bid.with_user(self.admin)._action_confirm_engagement()
        order.with_user(self.ops).action_reopen_bidding()
        self.assertEqual((order.status, order.bidding_round), ("bid_requested", 3))
        self.assertFalse(order._current_round_invitations())
        self.assertTrue(order.can_request_vendor_bids)
        self.assertEqual(prior_invitation.response_deadline, prior_deadline)
        self.assertEqual(current.response_deadline, new_deadline)
        self.assertEqual(
            set(order.invitation_ids.mapped("round_number")), {1, 2}
        )

    def test_reopened_round_with_partial_state_fails_closed(self):
        order, prior_invitation, _bid, _engagement, _deadline = (
            self._reopened_unsolicited_order()
        )
        prior_authorization = self.env[
            "trucalc.order.vendor.authorization"
        ].sudo().with_context(active_test=False).search([
            ("order_id", "=", order.id),
            ("source", "=", "invitation"),
            ("round_number", "=", 1),
        ])
        self.assertEqual(len(prior_authorization), 1)
        self.env.cr.execute(
            "UPDATE trucalc_order_vendor_authorization SET round_number = %s "
            "WHERE id = %s",
            (order.bidding_round, prior_authorization.id),
        )
        prior_authorization.invalidate_recordset(["round_number"])
        order.invalidate_recordset([
            "can_request_vendor_bids", "current_round_has_solicitation",
        ])
        self.assertFalse(order._current_round_invitations())
        self.assertFalse(order.current_round_has_solicitation)
        self.assertFalse(order.can_request_vendor_bids)
        with self.assertRaisesRegex(ValidationError, "clean, unsolicited reopened"):
            order.action_open_request_bids_wizard()
        with self.assertRaisesRegex(ValidationError, "clean, unsolicited reopened"):
            order.action_request_vendor_bids(self.vendor_b, self._deadline())
        self.assertEqual((order.status, order.bidding_round), ("bid_requested", 2))
        self.assertEqual(prior_invitation.round_number, 1)

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
            self.assertEqual(invitation.service_type, "evaluation")
            self.assertEqual(invitation.standard_fee, 500)
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
        invalid_vendors = (self.inactive_vendor, self.no_fee_vendor)
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
        self.assertEqual(
            {invitation.vendor_id: invitation.standard_fee for invitation in invitations},
            {self.vendor_a: 500, self.vendor_b: 600},
        )
        self.assertEqual(set(invitations.mapped("service_type")), {"evaluation"})
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
        snapshots = {
            invitation.id: (invitation.service_type, invitation.standard_fee)
            for invitation in order.invitation_ids
        }
        new = self._deadline(4)
        order.with_user(self.ops).action_extend_bid_deadline(new)
        self.assertEqual(set(order.invitation_ids.mapped("response_deadline")), {new})
        self.assertEqual(set(order.vendor_authorization_ids.mapped("expires_at")), {new})
        self.assertEqual(order.invitation_ids.ids, invitation_ids)
        self.assertEqual(order.vendor_authorization_ids.ids, authorization_ids)
        self.assertEqual(
            snapshots,
            {invitation.id: (invitation.service_type, invitation.standard_fee)
             for invitation in order.invitation_ids},
        )
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
        self.assertIn(self.wrong_type_vendor, displayed)
        self.assertNotIn(self.inactive_vendor, displayed)
        fees = {line.vendor_id: line.standard_fee for line in wizard.line_ids}
        self.assertEqual((fees[self.vendor_a], fees[self.vendor_b]), (500, 600))
        self.assertEqual(fees[self.wrong_type_vendor], 700)
        displays = {
            line.vendor_id: line.standard_fee_display for line in wizard.line_ids
        }
        self.assertEqual(displays[self.vendor_a], "500.00")
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
        self.assertEqual(invited.standard_fee, 500)
        self.assertEqual(invited.standard_fee_display, "500.00")
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

    def test_manage_displays_current_round_snapshot_and_current_available_fee(self):
        order = self._accepted_order()
        deadline = self._deadline()
        order.action_request_vendor_bids(self.vendor_a, deadline)
        invitation = order.invitation_ids
        authorization_ids = order.vendor_authorization_ids.ids
        audit_ids = self.env["trucalc.bid.audit"].search([
            ("order_id", "=", order.id),
        ]).ids
        message_ids = order.message_ids.ids

        self.vendor_a.fee_schedule_ids.filtered(
            lambda fee: fee.service_type == "evaluation"
        ).fee = 550
        self.vendor_b.fee_schedule_ids.filtered(
            lambda fee: fee.service_type == "evaluation"
        ).fee = 650
        action = order.action_open_manage_bid_requests_wizard()
        wizard = self.env[action["res_model"]].with_user(self.admin).with_context(
            action["context"]
        ).create({})
        invited = wizard.line_ids.filtered(lambda line: line.vendor_id == self.vendor_a)
        available = wizard.line_ids.filtered(lambda line: line.vendor_id == self.vendor_b)
        self.assertTrue(invited.already_invited)
        self.assertFalse(invited.selected)
        self.assertEqual((invited.standard_fee, invited.standard_fee_display), (500, "500.00"))
        self.assertFalse(available.already_invited)
        self.assertEqual((available.standard_fee, available.standard_fee_display), (650, "650.00"))
        self.assertEqual(invitation.standard_fee, 500)
        self.assertEqual(order.invitation_ids, invitation)
        self.assertEqual(order.vendor_authorization_ids.ids, authorization_ids)
        self.assertEqual(self.env["trucalc.bid.audit"].search([
            ("order_id", "=", order.id),
        ]).ids, audit_ids)
        self.assertEqual(order.message_ids.ids, message_ids)
        self.assertFalse(order.bid_ids)
        self.assertFalse(order.assigned_vendor_id)
        self.assertEqual(order.vendor_fee, 0)
        self.assertEqual((order.status, order.bidding_round), ("bid_requested", 1))

    def test_manage_does_not_fabricate_unknown_historical_fee(self):
        order = self._accepted_order()
        order.action_request_vendor_bids(self.vendor_a, self._deadline())
        invitation = order.invitation_ids
        self.env.cr.execute(
            "UPDATE trucalc_bid_invitation SET standard_fee = NULL WHERE id = %s",
            (invitation.id,),
        )
        invitation.invalidate_recordset(["standard_fee"])
        self.vendor_a.fee_schedule_ids.filtered(
            lambda fee: fee.service_type == "evaluation"
        ).fee = 999
        action = order.action_open_manage_bid_requests_wizard()
        wizard = self.env[action["res_model"]].with_user(self.admin).with_context(
            action["context"]
        ).create({})
        line = wizard.line_ids.filtered(lambda item: item.vendor_id == self.vendor_a)
        self.assertTrue(line.already_invited)
        self.assertFalse(line.standard_fee)
        self.assertEqual(line.standard_fee_display, "Not recorded")
        self.assertFalse(invitation.standard_fee)

    def test_manage_uses_current_round_invitation_only(self):
        order = self._accepted_order()
        order.action_request_vendor_bids(self.vendor_a | self.vendor_b, self._deadline())
        prior_a = order.invitation_ids.filtered(lambda item: item.vendor_id == self.vendor_a)
        self.vendor_a.fee_schedule_ids.filtered(
            lambda fee: fee.service_type == "evaluation"
        ).fee = 575
        self.vendor_b.fee_schedule_ids.filtered(
            lambda fee: fee.service_type == "evaluation"
        ).fee = 675
        order._controlled_lifecycle_write({"bidding_round": 2})
        current_a = self.env["trucalc.bid.invitation"].with_user(self.admin).create({
            "order_id": order.id,
            "vendor_id": self.vendor_a.id,
            "response_deadline": self._deadline(3),
        })
        action = order.action_open_manage_bid_requests_wizard()
        wizard = self.env[action["res_model"]].with_user(self.admin).with_context(
            action["context"]
        ).create({})
        line_a = wizard.line_ids.filtered(lambda line: line.vendor_id == self.vendor_a)
        line_b = wizard.line_ids.filtered(lambda line: line.vendor_id == self.vendor_b)
        self.assertTrue(line_a.already_invited)
        self.assertEqual(line_a.standard_fee, 575)
        self.assertEqual(current_a.standard_fee, 575)
        self.assertEqual(prior_a.standard_fee, 500)
        self.assertFalse(line_b.already_invited)
        self.assertEqual(line_b.standard_fee, 675)

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
        self.assertNotIn("due_date", projection.fields_get())
        self.assertFalse(projection.vendor_delivery_date)
        self.assertEqual(projection.response_deadline, deadline)
        self.assertEqual(projection.solicitation_standard_fee, 500)
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

    def test_snapshot_is_immutable_and_later_invitation_uses_current_fee(self):
        order = self._accepted_order()
        order.action_request_vendor_bids(self.vendor_a, self._deadline())
        invitation = order.invitation_ids
        self.assertEqual(invitation.standard_fee, 500)
        with self.assertRaises(AccessError):
            invitation.write({"standard_fee": 999})
        with self.assertRaises(AccessError):
            invitation.write({"service_type": "appraisal"})
        fee = self.vendor_a.fee_schedule_ids.filtered(
            lambda record: record.service_type == "evaluation"
        )
        fee.fee = 550
        self.assertEqual(invitation.standard_fee, 500)

        later = self._accepted_order()
        later.action_request_vendor_bids(self.vendor_a, self._deadline())
        self.assertEqual(later.invitation_ids.standard_fee, 550)
        self.assertFalse(later.bid_ids)
        self.assertFalse(later.assigned_vendor_id)
        self.assertEqual(later.vendor_fee, 0)

    def test_invitation_create_rejects_crafted_snapshot_values(self):
        order = self._accepted_order()
        order.action_bid_requested()
        with self.assertRaises(AccessError):
            self.env["trucalc.bid.invitation"].create({
                "order_id": order.id,
                "vendor_id": self.vendor_a.id,
                "response_deadline": self._deadline(),
                "service_type": "appraisal",
                "standard_fee": 1,
            })
