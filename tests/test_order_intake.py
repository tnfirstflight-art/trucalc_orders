from lxml import html

from odoo import Command, fields, tools
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import Form, TransactionCase, tagged


@tagged("post_install", "-at_install", "trucalc_order_intake")
class TestOrderIntake(TransactionCase):
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
                "group_bank_requestor",
                "group_bank_view_only",
                "group_vendor_portal",
            )
        }
        cls.vendor = cls.env["trucalc.vendor"].create({
            "name": "4B2C0 Vendor",
            "vendor_type": "appraiser",
        })
        cls.bank = cls.env["res.company"].create({"name": "4B2C0 Bank"})
        cls.admin = cls._user("4b2c0-admin", "group_trucalc_admin")
        cls.ops = cls._user("4b2c0-ops", "group_trucalc_operations")
        cls.reviewer = cls._user("4b2c0-reviewer", "group_trucalc_reviewer")
        cls.bank_users = [
            cls._user("4b2c0-bank-admin", "group_bank_admin", bank=cls.bank),
            cls._user("4b2c0-bank-requestor", "group_bank_requestor", bank=cls.bank),
            cls._user("4b2c0-bank-view", "group_bank_view_only", bank=cls.bank),
        ]
        cls.vendor_user = cls._user(
            "4b2c0-vendor", "group_vendor_portal", vendor=cls.vendor
        )
        cls.portal_user = cls.env["res.users"].with_context(
            no_reset_password=True
        ).create({
            "name": "4B2C0 Portal",
            "login": "4b2c0-portal",
            "email": "4b2c0-portal@example.test",
            "group_ids": [Command.set([cls.env.ref("base.group_portal").id])],
        })
        cls.internal_user = cls.env["res.users"].with_context(
            no_reset_password=True
        ).create({
            "name": "4B2C0 Internal",
            "login": "4b2c0-internal",
            "email": "4b2c0-internal@example.test",
            "group_ids": [Command.set([cls.env.ref("base.group_user").id])],
        })

    @classmethod
    def _user(cls, login, group_name, bank=False, vendor=False):
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": login,
            "login": login,
            "email": "%s@example.test" % login,
            "group_ids": [Command.set([cls.groups[group_name].id])],
            "trucalc_bank_company_id": bank.id if bank else False,
            "trucalc_vendor_id": vendor.id if vendor else False,
        })

    def _order(self):
        return self.env["trucalc.order"].with_user(self.admin).create({
            "borrower": "Intake Test",
            "property_address": "1 Intake Way",
            "company_id": self.env.company.id,
            "service_type": "evaluation",
            "due_date": fields.Date.add(fields.Date.today(), days=14),
        })

    def test_order_date_due_date_creation_chatter_and_detail_presentation(self):
        self.admin.tz = "America/Los_Angeles"
        legacy = self.env["trucalc.order"].sudo().search([
            ("order_number", "=", "TC-00005"),
        ], limit=1)
        legacy_order_date = legacy.order_date
        order_form = Form(
            self.env["trucalc.order"].with_user(self.admin),
            view="trucalc_orders.view_trucalc_order_form",
        )
        today_display = fields.Date.context_today(
            self.env["trucalc.order"].with_user(self.admin)
        ).strftime("%m/%d/%Y")
        self.assertEqual(order_form.order_date_display, today_display)
        with self.assertRaises(AssertionError):
            order_form.order_date_display = "01/01/2000"
        order_form.borrower = "Form Intake Test"
        order_form.property_address = "2 Intake Way"
        order_form.service_type = "evaluation"
        order_form.due_date = fields.Date.add(fields.Date.today(), days=14)
        form_order = order_form.save()
        self.assertEqual(
            form_order.order_date,
            fields.Date.context_today(form_order.with_user(self.admin)),
        )
        self.assertEqual(form_order.order_date_display, today_display)
        order = self._order()
        self.assertEqual(
            order.order_date,
            fields.Date.context_today(order.with_user(self.admin)),
        )
        self.assertEqual(order.order_date_display, order.order_date.strftime("%m/%d/%Y"))
        messages = order.message_ids.filtered(
            lambda message: "Order Created" in (message.body or "")
        )
        self.assertEqual(len(messages), 1)
        text = html.fromstring(f"<div>{messages.body}</div>").text_content()
        self.assertIn("Order Created", text)
        self.assertIn(order.order_date.strftime("%m/%d/%Y"), text)
        self.assertIn(
            tools.format_datetime(
                order.with_user(self.admin).env, order.create_date,
                tz=self.admin.tz, dt_format="medium",
            ),
            text,
        )
        with self.assertRaises(AccessError):
            self.env["trucalc.order"].with_user(self.admin).create({
                "borrower": "Forged Date", "property_address": "1 Date Way",
                "due_date": fields.Date.add(fields.Date.today(), days=1),
                "order_date": fields.Date.add(fields.Date.today(), days=-1),
            })
        with self.assertRaises(AccessError):
            order.write({"order_date": fields.Date.add(order.order_date, days=1)})
        with self.assertRaises(ValidationError):
            order.write({"due_date": False})
        with self.assertRaises(ValidationError):
            self.env["trucalc.order"].with_user(self.admin).create({
                "borrower": "Missing Due", "property_address": "1 Due Way",
            })
        if legacy:
            self.assertEqual(legacy.order_date, legacy_order_date)

        arch = html.fromstring(
            self.env.ref("trucalc_orders.view_trucalc_order_form").arch
        )
        property_group = arch.xpath("//group[@string='Property Information']")[0]
        self.assertFalse(property_group.xpath("./group"))
        self.assertEqual(
            [field.get("name") for field in property_group.xpath("./field")],
            ["property_address", "city", "state", "zip_code", "property_type"],
        )
        self.assertTrue(arch.xpath("//field[@name='order_date_display']"))
        due_date = arch.xpath("//field[@name='due_date']")[0]
        self.assertEqual(due_date.get("required"), "1")

    def _assert_no_vendor_lifecycle(self, order):
        self.assertEqual(order.bidding_round, 0)
        self.assertFalse(order.assigned_vendor_id)
        self.assertFalse(order.invitation_ids)
        self.assertFalse(order.bid_ids)
        self.assertFalse(order.vendor_authorization_ids)
        self.assertFalse(self.env["trucalc.vendor.order"].sudo().search([
            ("order_number", "=", order.order_number),
        ]))

    def test_admin_and_operations_accept_new_without_vendor_lifecycle(self):
        for user in (self.admin, self.ops):
            order = self._order()
            self.assertTrue(order.with_user(user).action_accept_request())
            self.assertEqual(order.status, "accepted")
            self._assert_no_vendor_lifecycle(order)
            self.assertTrue(order.message_ids.filtered(
                lambda message: "Request accepted" in (message.body or "")
            ))

    def test_accept_is_role_and_state_protected(self):
        denied = [
            self.reviewer,
            *self.bank_users,
            self.vendor_user,
            self.portal_user,
            self.internal_user,
        ]
        for user in denied:
            with self.assertRaises(AccessError):
                self._order().with_user(user).action_accept_request()
        order = self._order()
        with self.assertRaises(AccessError):
            order.write({"status": "accepted"})
        order.action_accept_request()
        with self.assertRaises(ValidationError):
            order.action_accept_request()
        with self.assertRaises(ValidationError):
            order.action_decline_request("Too late")

    def test_decline_wizard_persists_protected_reason(self):
        for user in (self.admin, self.ops):
            order = self._order()
            action = order.with_user(user).action_open_decline_wizard()
            self.assertEqual(action["res_model"], "trucalc.order.decline.wizard")
            wizard = self.env["trucalc.order.decline.wizard"].with_user(user).create({
                "order_id": order.id,
                "reason": "Unsupported assignment scope",
            })
            wizard.action_decline()
            self.assertEqual(order.status, "declined")
            self.assertEqual(order.decline_reason, "Unsupported assignment scope")
            self._assert_no_vendor_lifecycle(order)
            self.assertTrue(order.message_ids.filtered(
                lambda message: "Unsupported assignment scope" in (message.body or "")
            ))
            with self.assertRaises(AccessError):
                order.write({"decline_reason": "Tampered"})

    def test_decline_rejects_roles_blank_reason_and_direct_writes(self):
        denied = [
            self.reviewer,
            *self.bank_users,
            self.vendor_user,
            self.portal_user,
            self.internal_user,
        ]
        for user in denied:
            with self.assertRaises(AccessError):
                self._order().with_user(user).action_decline_request("Denied")
        for reason in (False, "", "   "):
            with self.assertRaises(ValidationError):
                self._order().action_decline_request(reason)
        order = self._order()
        with self.assertRaises(AccessError):
            order.write({"status": "declined"})
        order.action_decline_request("Duplicate request")
        for status in ("new", "accepted", "bid_requested", "cancelled", "completed"):
            with self.assertRaises(AccessError):
                order.write({"status": status})
        with self.assertRaises(ValidationError):
            order.action_decline_request("Again")
        with self.assertRaises(ValidationError):
            order.action_bid_requested()
        with self.assertRaises(ValidationError):
            order.action_cancelled()

    def test_bidding_requires_accepted_and_audits_prior_state(self):
        new_order = self._order()
        with self.assertRaises(ValidationError):
            new_order.action_bid_requested()
        order = self._order()
        order.action_accept_request()
        with self.assertRaises(AccessError):
            order.write({"status": "bid_requested"})
        order.action_bid_requested()
        self.assertEqual((order.status, order.bidding_round), ("bid_requested", 1))
        audit = self.env["trucalc.bid.audit"].search([
            ("action", "=", "bidding_started"),
            ("order_id", "=", order.id),
        ], limit=1)
        self.assertEqual(audit.old_values, {"status": "accepted", "bidding_round": 0})

    def test_new_cannot_jump_downstream_or_cancel(self):
        order = self._order()
        for status in (
            "bid_requested", "assigned", "report_received", "reviewer_assigned",
            "under_review", "completed", "cancelled", "declined",
        ):
            with self.assertRaises(AccessError):
                order.write({"status": status})
        with self.assertRaises(AccessError):
            order.action_report_received()
        with self.assertRaises(AccessError):
            order.action_assign_reviewer()
        with self.assertRaises(AccessError):
            order.action_start_review()
        with self.assertRaises(AccessError):
            order.action_complete_review()
        with self.assertRaises(ValidationError):
            order.action_cancelled()

    def test_stale_disposition_calls_fail_after_first_result(self):
        order = self._order()
        stale = self.env["trucalc.order"].with_user(self.ops).browse(order.id)
        self.assertEqual(stale.status, "new")
        order.with_user(self.admin).action_accept_request()
        with self.assertRaises(ValidationError):
            stale.action_decline_request("Stale decline")

        order = self._order()
        stale = self.env["trucalc.order"].with_user(self.ops).browse(order.id)
        self.assertEqual(stale.status, "new")
        order.with_user(self.admin).action_decline_request("First decline")
        with self.assertRaises(ValidationError):
            stale.action_accept_request()

        order = self._order()
        stale = self.env["trucalc.order"].with_user(self.ops).browse(order.id)
        order.with_user(self.admin).action_accept_request()
        with self.assertRaises(ValidationError):
            stale.action_accept_request()

        order = self._order()
        stale = self.env["trucalc.order"].with_user(self.ops).browse(order.id)
        order.with_user(self.admin).action_decline_request("First decline")
        with self.assertRaises(ValidationError):
            stale.action_decline_request("Second decline")

    def test_decline_wizard_acl_is_minimal(self):
        model = self.env["trucalc.order.decline.wizard"]
        for user in (self.admin, self.ops):
            self.assertTrue(model.with_user(user).has_access("create"))
        for user in (
            self.reviewer, *self.bank_users, self.vendor_user,
            self.portal_user, self.internal_user,
        ):
            self.assertFalse(model.with_user(user).has_access("create"))
