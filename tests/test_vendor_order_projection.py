from odoo import Command, fields
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "trucalc_vendor_order_projection")
class TestVendorOrderProjection(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "4B2A Admin", "login": "4b2a-admin",
            "group_ids": [Command.set([
                cls.env.ref("trucalc_orders.group_trucalc_admin").id
            ])],
        })
        cls.vendor = cls.env["trucalc.vendor"].create({
            "name": "4B2A Vendor", "vendor_type": "appraiser",
        })
        cls.other_vendor = cls.env["trucalc.vendor"].create({
            "name": "4B2A Other Vendor", "vendor_type": "appraiser",
        })
        cls.vendor_user = cls.env["res.users"].with_context(
            no_reset_password=True
        ).create({
            "name": "4B2A Vendor User", "login": "4b2a-vendor-user",
            "group_ids": [Command.set([
                cls.env.ref("trucalc_orders.group_vendor_portal").id
            ])],
            "trucalc_vendor_id": cls.vendor.id,
        })

    def _order(self):
        order = self.env["trucalc.order"].with_user(self.admin).create({
            "borrower": "Forbidden Borrower",
            "property_address": "1 Safe Street",
            "city": "Memphis", "state": "TN", "zip_code": "38103",
            "company_id": self.env.company.id,
            "service_type": "evaluation", "property_type": "single_family",
        })
        order.with_user(self.admin).action_bid_requested()
        return order

    def _invitation(self, vendor=None):
        return self.env["trucalc.bid.invitation"].with_user(self.admin).create({
            "order_id": self._order().id,
            "vendor_id": (vendor or self.vendor).id,
            "response_deadline": fields.Datetime.add(fields.Datetime.now(), days=1),
        })

    def test_structure_acl_and_immutability(self):
        model = self.env["trucalc.vendor.order"].with_user(self.vendor_user)
        self.assertFalse(model._auto)
        self.assertEqual(model._rec_name, "order_number")
        public = set(model.fields_get())
        forbidden = {
            "vendor_id", "order_id", "company_id", "requestor_id", "reviewer_id",
            "document_ids", "invitation_id", "authorization_id", "message_ids",
            "activity_ids", "attachment_ids", "borrower", "loan_number",
            "loan_amount", "notes", "review_fee", "fee_override",
        }
        self.assertFalse(public & forbidden)
        self.assertFalse({"mail.thread", "mail.activity.mixin"} & set(model._inherit))
        with self.assertRaises(AccessError):
            model.create({"order_number": "forged"})

    def test_invitation_projection_and_tenant_rule(self):
        invitation = self._invitation()
        own = self.env["trucalc.vendor.order"].with_user(self.vendor_user).search([])
        self.assertEqual(len(own), 1)
        self.assertEqual(own.order_number, invitation.order_id.order_number)
        self.assertEqual(own.vendor_phase, "invitation")
        self.assertEqual(own.vendor_status, "open_for_bid")
        self.assertEqual(own.response_deadline, invitation.response_deadline)
        self.assertFalse(own.due_date)
        self.assertFalse(own.is_assigned)
        self.assertEqual(own.agreed_vendor_fee, 0)

        other = self._invitation(vendor=self.other_vendor)
        self.assertNotIn(other.order_id.order_number, own.mapped("order_number"))
        self.env.cr.execute(
            "UPDATE res_users SET trucalc_vendor_id = NULL WHERE id = %s",
            [self.vendor_user.id],
        )
        self.vendor_user.invalidate_recordset(["trucalc_vendor_id"])
        self.assertFalse(
            self.env["trucalc.vendor.order"].with_user(self.vendor_user).search([])
        )

    def test_legacy_and_raw_models_fail_closed(self):
        projection = self.env["trucalc.vendor.order"].with_user(self.vendor_user)
        self.assertFalse(projection.search([]))
        self.assertFalse(projection.search_count([]))
        self.assertFalse(self.env["trucalc.order.vendor.authorization"].sudo().search([
            ("order_id", "=", 9),
        ]))
        self.assertFalse(self.env["trucalc.order"].with_user(
            self.vendor_user
        ).check_access_rights("read", raise_exception=False))
        self.assertFalse(self.env["trucalc.document"].with_user(
            self.vendor_user
        ).check_access_rights("read", raise_exception=False))

    def test_bid_hides_raw_order_relation(self):
        invitation = self._invitation()
        bid = invitation.with_user(self.vendor_user).action_vendor_create_option({
            "option_name": "Standard", "bid_amount": 500, "turn_time_days": 5,
        })
        vendor_bid = bid.with_user(self.vendor_user)
        visible_fields = vendor_bid.fields_get()
        for forbidden_field in ("order_id", "invitation_id", "company_id", "vendor_id"):
            self.assertNotIn(forbidden_field, visible_fields)
        self.assertIn("vendor_order_number", visible_fields)
        self.assertIn("round_number", visible_fields)
        self.assertEqual(vendor_bid.vendor_order_number, invitation.order_id.order_number)
        for forbidden_field in ("order_id", "invitation_id", "company_id", "vendor_id"):
            with self.assertRaises(AccessError):
                vendor_bid.read([forbidden_field])
