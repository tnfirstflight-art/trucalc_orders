from datetime import timedelta

from odoo import Command, fields, tools
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, tagged
from lxml import html


@tagged("post_install", "-at_install", "trucalc_vendor_bid_response")
class TestVendorBidResponse(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = cls._user("4b2c3-admin", "group_trucalc_admin")
        cls.ops = cls._user("4b2c3-ops", "group_trucalc_operations")
        cls.reviewer = cls._user("4b2c3-reviewer", "group_trucalc_reviewer")
        cls.vendor_a = cls.env["trucalc.vendor"].create({"name": "4B2C3 Vendor A"})
        cls.vendor_b = cls.env["trucalc.vendor"].create({"name": "4B2C3 Vendor B"})
        cls.env["trucalc.vendor.fee"].create([
            {"vendor_id": cls.vendor_a.id, "service_type": "evaluation", "fee": 0},
            {"vendor_id": cls.vendor_b.id, "service_type": "evaluation", "fee": 75},
        ])
        cls.vendor_user_a = cls._user(
            "4b2c3-vendor-a", "group_vendor_portal", cls.vendor_a
        )
        cls.vendor_user_b = cls._user(
            "4b2c3-vendor-b", "group_vendor_portal", cls.vendor_b
        )

    @classmethod
    def _user(cls, login, group, vendor=False):
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": login, "login": login, "email": f"{login}@example.test",
            "group_ids": [Command.set([cls.env.ref(f"trucalc_orders.{group}").id])],
            "trucalc_vendor_id": vendor.id if vendor else False,
        })

    def _order(self, due=True):
        order = self.env["trucalc.order"].with_user(self.admin).create({
            "borrower": "4B2C3 Borrower", "property_address": "3 Response Way",
            "company_id": self.env.company.id, "service_type": "evaluation",
            "due_date": fields.Date.add(fields.Date.today(), days=14) if due else False,
        })
        order.with_user(self.admin).action_accept_request()
        return order

    def _invitation(self, vendor=None):
        order = self._order()
        deadline = fields.Datetime.now() + timedelta(days=2)
        order.with_user(self.admin).action_request_vendor_bids(
            vendor or self.vendor_a, deadline
        )
        invitation = order.invitation_ids
        projection = self.env["trucalc.vendor.order"].with_user(
            self.vendor_user_a if invitation.vendor_id == self.vendor_a
            else self.vendor_user_b
        ).search([("order_number", "=", order.order_number)])
        return order, invitation, projection

    def test_snapshot_and_due_date_solicitation_requirement(self):
        order = self._order()
        original = order.due_date
        order.action_request_vendor_bids(
            self.vendor_a, fields.Datetime.now() + timedelta(days=1)
        )
        self.assertEqual(order.invitation_ids.requested_delivery_date, original)
        order.due_date = fields.Date.add(original, days=3)
        self.assertEqual(order.invitation_ids.requested_delivery_date, original)

        with self.assertRaises(ValidationError):
            self._order(due=False)

    def test_all_response_types_and_revision_audit(self):
        order, invitation, projection = self._invitation(self.vendor_a)
        self.assertEqual(projection.vendor_response_label, "Open for Response")
        bid = projection.action_vendor_response("standard_terms_accepted")
        self.env.flush_all()
        projection.invalidate_recordset()
        self.assertEqual(projection.vendor_response_label, "Submitted")
        self.assertEqual(bid.bid_amount, 0)
        self.assertEqual(bid.proposed_delivery_date, invitation.requested_delivery_date)
        self.assertEqual(bid.status, "submitted")
        self.assertTrue(bid.submitted_at)
        submitted_at = bid.submitted_at
        self.assertFalse(order.assigned_vendor_id)
        self.assertEqual(order.vendor_fee, 0)

        changed = fields.Date.add(invitation.requested_delivery_date, days=2)
        projection.action_vendor_response(
            "delivery_date_counter", proposed_fee=999,
            proposed_delivery_date=changed, comments="Schedule change",
        )
        self.assertEqual(bid.bid_amount, 0)
        self.assertEqual(bid.proposed_delivery_date, changed)
        self.assertEqual(bid.revision_count, 1)
        self.assertEqual(bid.submitted_at, submitted_at)
        self.assertTrue(bid.last_revised_at)

        counter_date = fields.Date.add(changed, days=1)
        projection.action_vendor_response(
            "fee_and_delivery_counter", proposed_fee=25,
            proposed_delivery_date=counter_date, comments="Full counter",
        )
        self.assertEqual((bid.bid_amount, bid.proposed_delivery_date), (25, counter_date))
        self.assertEqual(bid.revision_count, 2)
        self.assertEqual(self.env["trucalc.bid.audit"].search_count([
            ("bid_id", "=", bid.id), ("action", "=", "response_revised")
        ]), 2)

        projection.action_vendor_response("standard_terms_accepted")
        self.assertEqual(bid.revision_count, 3)
        self.assertFalse(bid.notes)

    def test_response_semantics_and_foreign_vendor_fail_closed(self):
        order, invitation, projection = self._invitation(self.vendor_b)
        requested = invitation.requested_delivery_date
        with self.assertRaises(ValidationError):
            projection.action_vendor_response(
                "delivery_date_counter", proposed_delivery_date=requested
            )
        with self.assertRaises(ValidationError):
            projection.action_vendor_response(
                "fee_and_delivery_counter", proposed_fee=75,
                proposed_delivery_date=fields.Date.add(requested, days=1),
            )
        with self.assertRaises(ValidationError):
            projection.action_vendor_response(
                "fee_and_delivery_counter", proposed_fee=-1,
                proposed_delivery_date=fields.Date.add(requested, days=1),
            )
        with self.assertRaises(AccessError):
            projection.with_user(self.vendor_user_a).action_vendor_response(
                "standard_terms_accepted"
            )
        self.assertFalse(order.bid_ids)

    def test_decline_retains_read_only_historical_projection(self):
        order, invitation, projection = self._invitation(self.vendor_a)
        with self.assertRaises(ValidationError):
            projection.action_vendor_decline("")
        projection.action_vendor_decline("Capacity unavailable")
        self.assertEqual(invitation.state, "declined")
        self.assertEqual(invitation.decline_reason, "Capacity unavailable")
        self.assertFalse(order.bid_ids)
        self.env.cr.execute(
            "SELECT active, deauthorization_reason "
            "FROM trucalc_order_vendor_authorization WHERE invitation_id = %s",
            (invitation.id,),
        )
        self.assertEqual(self.env.cr.fetchone(), (False, "declined"))
        history = self.env["trucalc.vendor.order"].with_user(
            self.vendor_user_a
        ).search([("order_number", "=", order.order_number)])
        history.invalidate_recordset()
        self.assertEqual(history.vendor_status, "declined")
        self.assertEqual(history.order_status, "bid_requested")
        self.assertEqual(history.vendor_response_label, "Declined")
        self.assertFalse(history.can_respond)
        self.assertEqual(history.vendor_decline_reason, "Capacity unavailable")
        with self.assertRaises(AccessError):
            history.action_vendor_response("standard_terms_accepted")

    def test_selection_preserves_client_due_date_and_reopen_clears_vendor_date(self):
        order, invitation, projection = self._invitation(self.vendor_b)
        due = order.due_date
        proposed = fields.Date.add(due, days=2)
        bid = projection.action_vendor_response(
            "fee_and_delivery_counter", proposed_fee=100,
            proposed_delivery_date=proposed,
        )
        other_invitation = self.env["trucalc.bid.invitation"].with_user(
            self.admin
        ).create({
            "order_id": order.id, "vendor_id": self.vendor_a.id,
            "response_deadline": invitation.response_deadline,
        })
        other_projection = self.env["trucalc.vendor.order"].with_user(
            self.vendor_user_a
        ).search([("order_number", "=", order.order_number)])
        other_bid = other_projection.action_vendor_response(
            "standard_terms_accepted"
        )
        self.assertTrue(bid.is_currently_selectable)
        self.assertTrue(other_bid.is_currently_selectable)
        for denied in (self.vendor_user_b, self.reviewer):
            with self.assertRaises(AccessError):
                bid.with_user(denied).action_select_bid()
        prior_message_ids = set(order.message_ids.ids)
        before = (
            order.status, order.assigned_vendor_id, order.vendor_fee,
            order.vendor_delivery_date, order.vendor_engaged_at,
            bid.status, other_bid.status, tuple(invitation.mapped("state")),
        )
        action = bid.with_user(self.ops).action_select_bid()
        self.assertEqual(action["res_model"], "trucalc.vendor.engagement.wizard")
        self.assertEqual(
            before,
            (order.status, order.assigned_vendor_id, order.vendor_fee,
             order.vendor_delivery_date, order.vendor_engaged_at,
             bid.status, other_bid.status, tuple(invitation.mapped("state"))),
        )
        wizard = self.env[action["res_model"]].with_user(self.ops).create({
            "bid_id": bid.id,
        })
        wizard.action_engage_vendor()
        self.env.flush_all()
        selected_projection = self.env["trucalc.vendor.order"].with_user(
            self.vendor_user_b
        ).search([("order_number", "=", order.order_number)])
        selected_projection.invalidate_recordset()
        self.assertEqual(selected_projection.order_status, "engaged")
        self.assertEqual(selected_projection.vendor_response_label, "Selected")
        self.assertEqual(selected_projection.proposed_fee, 100)
        self.assertEqual(bid.status, "selected")
        self.assertFalse(bid.is_currently_selectable)
        self.assertEqual(other_bid.status, "not_selected")
        self.assertFalse(other_bid.is_currently_selectable)
        self.assertEqual(order.assigned_vendor_id, self.vendor_b)
        self.assertEqual(order.vendor_fee, 100)
        self.assertEqual(order.vendor_delivery_date, proposed)
        self.assertTrue(order.vendor_engaged_at)
        self.assertEqual(order.due_date, due)
        new_messages = order.message_ids.filtered(
            lambda message: message.id not in prior_message_ids
        )
        self.assertEqual(len(new_messages), 1)
        body = str(new_messages.body)
        text = html.fromstring(f"<div>{body}</div>").text_content()
        expected_fee = tools.format_amount(
            self.env, 100, order.company_id.currency_id
        )
        expected_date = tools.format_date(self.env, proposed)
        self.assertIn("Vendor engaged", text)
        self.assertIn(self.vendor_b.name, text)
        self.assertIn(expected_fee, text)
        self.assertIn(expected_date, text)
        self.assertIn("Engaged", text)
        self.assertNotIn("None", text)
        self.assertNotIn("<script", body)
        audit = self.env["trucalc.bid.audit"].search([
            ("bid_id", "=", bid.id), ("action", "=", "vendor_engaged")
        ], limit=1)
        self.assertEqual(audit.old_values["order_status"], "bid_requested")
        self.assertEqual(audit.new_values["order_status"], "engaged")
        self.assertEqual(audit.new_values["assigned_vendor_id"], self.vendor_b.id)
        self.assertEqual(audit.new_values["vendor_fee"], 100)
        self.assertEqual(audit.new_values["vendor_delivery_date"], str(proposed))
        self.assertEqual(
            audit.new_values["vendor_engaged_at"],
            fields.Datetime.to_string(order.vendor_engaged_at),
        )
        order.with_user(self.admin).action_reopen_bidding()
        self.assertFalse(order.vendor_delivery_date)
        self.assertFalse(order.vendor_engaged_at)
        self.assertEqual(order.due_date, due)
        with self.assertRaises(AccessError):
            projection.action_vendor_response("standard_terms_accepted")

    def test_raw_bid_acl_is_removed(self):
        model = self.env["trucalc.bid"].with_user(self.vendor_user_a)
        for operation in ("read", "write", "create", "unlink"):
            self.assertFalse(model.check_access_rights(operation, raise_exception=False))
        with self.assertRaises(AccessError):
            model.create({"invitation_id": 1, "bid_amount": 1, "option_name": "Forged"})

    def test_decision_eligibility_disqualified_and_nonactionable_submitted(self):
        order, invitation, projection = self._invitation(self.vendor_b)
        bid = projection.action_vendor_response("standard_terms_accepted")
        self.assertTrue(bid.is_currently_selectable)
        bid.with_user(self.admin).action_correct_submitted(
            {"turn_time_days": 1}, "Complete legacy commercial validation fields"
        )
        bid.with_user(self.admin).action_disqualify("Commercial review")
        self.assertEqual(bid.status, "disqualified")
        self.assertFalse(bid.is_currently_selectable)

        later_order, later_invitation, later_projection = self._invitation(self.vendor_a)
        later_bid = later_projection.action_vendor_response("standard_terms_accepted")
        later_invitation.with_user(self.admin).action_set_response_deadline(
            fields.Datetime.now() - timedelta(seconds=1)
        )
        self.assertEqual(later_bid.status, "submitted")
        self.assertEqual(later_invitation.state, "expired")
        self.assertFalse(later_bid.is_currently_selectable)

    def test_decline_chatter_is_safely_single_escaped(self):
        order, invitation, projection = self._invitation(self.vendor_a)
        reason = 'I\'m unavailable; Smith & Jones; <test>; "quoted"; '
        reason += '<script>alert("x")</script>'
        projection.action_vendor_decline(reason)
        message = order.message_ids.filtered(
            lambda item: "declined the bid request" in (item.body or "")
        )[:1]
        body = str(message.body)
        rendered_text = html.fromstring(f"<div>{body}</div>").text_content()
        self.assertIn("I'm unavailable", rendered_text)
        self.assertIn("Smith & Jones", rendered_text)
        self.assertIn('<test>', rendered_text)
        self.assertIn('"quoted"', rendered_text)
        self.assertIn('<script>alert("x")</script>', rendered_text)
        self.assertNotIn("&amp;#39;", body)
        self.assertNotIn("&amp;amp;", body)
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;", body)

    def test_internal_views_show_currency_and_select_first(self):
        order_arch = html.fromstring(
            self.env.ref("trucalc_orders.view_trucalc_order_form").arch
        )
        vendor_fee = order_arch.xpath("//field[@name='vendor_fee']")
        self.assertEqual(len(vendor_fee), 1)
        self.assertEqual(vendor_fee[0].get("widget"), "monetary")
        response_list = order_arch.xpath(
            "//page[@string='Vendor Responses']/field[@name='bid_ids']/list"
        )[0]
        self.assertIn("o_trucalc_vendor_responses", response_list.get("class"))
        children = [child for child in response_list if isinstance(child.tag, str)]
        decision_buttons = children[:4]
        self.assertTrue(all(child.tag == "button" for child in decision_buttons))
        self.assertEqual(
            [button.get("string") for button in decision_buttons],
            ["Select", "Selected", "Not Selected", "Disqualified"],
        )
        self.assertEqual(
            [button.get("invisible") for button in decision_buttons],
            [
                "not is_currently_selectable",
                "status != 'selected'",
                "status != 'not_selected'",
                "status != 'disqualified'",
            ],
        )
        self.assertFalse(decision_buttons[0].get("disabled"))
        for button, semantic_class in zip(
            decision_buttons[1:], ("btn-success", "btn-secondary", "btn-danger")
        ):
            self.assertFalse(button.get("name"))
            self.assertFalse(button.get("type"))
            self.assertFalse(button.get("special"))
            self.assertFalse(button.get("disabled"))
            self.assertIn(semantic_class, button.get("class"))
            self.assertIn("opacity-100", button.get("class"))
        status_field = children[4]
        self.assertEqual((status_field.tag, status_field.get("name")),
                         ("field", "status"))
        self.assertEqual(status_field.get("column_invisible"), "True")
        self.assertFalse(response_list.xpath("./field[@string='Decision']"))
        status_labels = dict(self.env["trucalc.bid"]._fields["status"].selection)
        self.assertEqual(status_labels["selected"], "Selected")
        self.assertEqual(status_labels["not_selected"], "Not Selected")
        self.assertEqual(status_labels["disqualified"], "Disqualified")
        for field_name in ("solicited_standard_fee", "bid_amount"):
            field = response_list.xpath(f"./field[@name='{field_name}']")[0]
            self.assertEqual(field.get("widget"), "monetary")
        for field_name in ("requested_delivery_date", "proposed_delivery_date"):
            field = response_list.xpath(f"./field[@name='{field_name}']")[0]
            self.assertIn("'numeric': True", field.get("options"))

        bid_arch = html.fromstring(
            self.env.ref("trucalc_orders.view_trucalc_bid_tree").arch
        )
        self.assertIn("o_trucalc_vendor_responses", bid_arch.get("class"))
        bid_children = [child for child in bid_arch if isinstance(child.tag, str)]
        self.assertEqual(
            [button.get("string") for button in bid_children[:4]],
            ["Select", "Selected", "Not Selected", "Disqualified"],
        )
        self.assertTrue(all(button.tag == "button" for button in bid_children[:4]))
        for button, semantic_class in zip(
            bid_children[1:4], ("btn-success", "btn-secondary", "btn-danger")
        ):
            self.assertFalse(button.get("name"))
            self.assertFalse(button.get("type"))
            self.assertFalse(button.get("special"))
            self.assertIn(semantic_class, button.get("class"))
            self.assertIn("opacity-100", button.get("class"))
        self.assertEqual(bid_children[4].get("name"), "status")
        self.assertEqual(bid_children[4].get("column_invisible"), "True")
        self.assertFalse(bid_arch.xpath("./field[@string='Decision']"))
        for field_name in ("requested_delivery_date", "proposed_delivery_date"):
            field = bid_arch.xpath(f"./field[@name='{field_name}']")[0]
            self.assertIn("'numeric': True", field.get("options"))
