from odoo import Command, fields
from odoo.exceptions import AccessError
from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install", "trucalc_vendor_order_portal")
class TestVendorOrderPortal(HttpCase):
    password = "4B2B-portal-test"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "4B2B Admin",
            "login": "4b2b-admin",
            "password": cls.password,
            "group_ids": [Command.set([
                cls.env.ref("trucalc_orders.group_trucalc_admin").id
            ])],
        })
        cls.vendor_a = cls.env["trucalc.vendor"].create({
            "name": "4B2B Vendor A", "vendor_type": "appraiser",
        })
        cls.vendor_b = cls.env["trucalc.vendor"].create({
            "name": "4B2B Vendor B", "vendor_type": "appraiser",
        })
        cls.vendor_user_a = cls._create_vendor_user("a", cls.vendor_a)
        cls.vendor_user_b = cls._create_vendor_user("b", cls.vendor_b)
        cls.plain_portal_user = cls.env["res.users"].with_context(
            no_reset_password=True
        ).create({
            "name": "4B2B Plain Portal",
            "login": "4b2b-plain-portal",
            "password": cls.password,
            "group_ids": [Command.set([cls.env.ref("base.group_portal").id])],
        })

    @classmethod
    def _create_vendor_user(cls, suffix, vendor):
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": f"4B2B Vendor User {suffix.upper()}",
            "login": f"4b2b-vendor-{suffix}",
            "password": cls.password,
            "group_ids": [Command.set([
                cls.env.ref("trucalc_orders.group_vendor_portal").id
            ])],
            "trucalc_vendor_id": vendor.id,
        })

    @classmethod
    def _authorized_order(cls, vendor, address):
        order = cls.env["trucalc.order"].with_user(cls.admin).create({
            "borrower": f"Forbidden Borrower {address}",
            "property_address": address,
            "city": "Memphis",
            "state": "TN",
            "zip_code": "38103",
            "company_id": cls.env.company.id,
            "service_type": "evaluation",
            "property_type": "single_family",
        })
        order.with_user(cls.admin).action_bid_requested()
        invitation = cls.env["trucalc.bid.invitation"].with_user(cls.admin).create({
            "order_id": order.id,
            "vendor_id": vendor.id,
            "response_deadline": fields.Datetime.add(fields.Datetime.now(), days=1),
        })
        projection = cls.env["trucalc.vendor.order"].sudo().search([
            ("order_number", "=", order.order_number),
            ("vendor_id", "=", vendor.id),
        ])
        return order, invitation, projection

    def _login(self, user):
        self.authenticate(user.login, self.password)

    def test_anonymous_and_non_vendor_fail_closed(self):
        order, invitation, projection = self._authorized_order(
            self.vendor_a, "401 Vendor A Street"
        )
        self.authenticate(None, None)
        response = self.url_open("/my/trucalc/orders", allow_redirects=False)
        self.assertIn(response.status_code, (302, 303))

        self._login(self.plain_portal_user)
        self.assertEqual(self.url_open("/my/trucalc/orders").status_code, 404)
        self.assertEqual(
            self.url_open(f"/my/trucalc/orders/{order.order_number}").status_code,
            404,
        )

    def test_vendor_home_list_detail_and_cross_vendor_idor(self):
        order_a, invitation_a, projection_a = self._authorized_order(
            self.vendor_a, "411 Vendor A Street"
        )
        order_b, invitation_b, projection_b = self._authorized_order(
            self.vendor_b, "422 Vendor B Street"
        )

        self._login(self.vendor_user_a)
        home = self.url_open("/my").text
        self.assertIn("My TruCalc Orders", home)
        listing_a = self.url_open("/my/trucalc/orders").text
        self.assertIn(order_a.order_number, listing_a)
        self.assertIn("411 Vendor A Street", listing_a)
        self.assertNotIn(order_b.order_number, listing_a)
        self.assertNotIn("422 Vendor B Street", listing_a)
        detail_a = self.url_open(
            f"/my/trucalc/orders/{order_a.order_number}"
        ).text
        self.assertIn(order_a.order_number, detail_a)
        self.assertIn("411 Vendor A Street", detail_a)
        self.assertEqual(
            self.url_open(f"/my/trucalc/orders/{order_b.order_number}").status_code,
            404,
        )

        self._login(self.vendor_user_b)
        listing_b = self.url_open("/my/trucalc/orders").text
        self.assertIn(order_b.order_number, listing_b)
        self.assertNotIn(order_a.order_number, listing_b)
        self.assertEqual(
            self.url_open(f"/my/trucalc/orders/{order_a.order_number}").status_code,
            404,
        )

    def test_empty_missing_mapping_and_forged_context_fail_closed(self):
        order_b, invitation_b, projection_b = self._authorized_order(
            self.vendor_b, "433 Vendor B Street"
        )
        self._login(self.vendor_user_a)
        empty = self.url_open(
            "/my/trucalc/orders?default_vendor_id=%s&default_order_id=%s"
            "&allowed_company_ids=%s"
            % (self.vendor_b.id, order_b.id, order_b.company_id.id)
        ).text
        self.assertIn("no authorized TruCalc orders", empty)
        self.assertNotIn(order_b.order_number, empty)
        self.assertNotIn("433 Vendor B Street", empty)

        projection_model = self.env["trucalc.vendor.order"].with_user(
            self.vendor_user_a
        )
        self.env.cr.execute(
            "UPDATE res_users SET trucalc_vendor_id = NULL WHERE id = %s",
            [self.vendor_user_a.id],
        )
        self.vendor_user_a.invalidate_recordset(["trucalc_vendor_id"])
        self.assertFalse(projection_model.search([]))

    def test_revocation_expiration_legacy_and_raw_model_denial(self):
        order, invitation, projection = self._authorized_order(
            self.vendor_a, "444 Revoked Street"
        )
        self._login(self.vendor_user_a)
        self.assertEqual(
            self.url_open(f"/my/trucalc/orders/{order.order_number}").status_code,
            200,
        )

        invitation.with_user(self.admin).action_revoke()
        self.assertEqual(
            self.url_open(f"/my/trucalc/orders/{order.order_number}").status_code,
            404,
        )
        self.assertNotIn(order.order_number, self.url_open("/my/trucalc/orders").text)

        expired_order, expired_invitation, expired_projection = self._authorized_order(
            self.vendor_a, "455 Expired Street"
        )
        expired_invitation.with_user(self.admin).action_set_response_deadline(
            fields.Datetime.add(fields.Datetime.now(), seconds=-1)
        )
        self.assertNotIn(
            expired_order.order_number, self.url_open("/my/trucalc/orders").text
        )
        self.assertFalse(self.env["trucalc.vendor.order"].with_user(
            self.vendor_user_a
        ).search([("order_number", "=", "TC-00005")]))
        self.assertFalse(self.env["trucalc.order"].with_user(
            self.vendor_user_a
        ).check_access_rights("read", raise_exception=False))
        self.assertFalse(self.env["trucalc.document"].with_user(
            self.vendor_user_a
        ).check_access_rights("read", raise_exception=False))

    def test_projection_is_read_only_and_rendered_output_is_safe(self):
        order, invitation, projection = self._authorized_order(
            self.vendor_a, "466 Safe Output Street"
        )
        vendor_projection = projection.with_user(self.vendor_user_a)
        with self.assertRaises(AccessError):
            vendor_projection.create({"order_number": "forged"})
        with self.assertRaises(AccessError):
            vendor_projection.write({"property_address": "forged"})
        with self.assertRaises(AccessError):
            vendor_projection.unlink()

        self._login(self.vendor_user_a)
        rendered = self.url_open(
            f"/my/trucalc/orders/{order.order_number}"
        ).text
        self.assertIn("466 Safe Output Street", rendered)
        for forbidden in (
            "Forbidden Borrower",
            "order_id",
            "vendor_id",
            "company_id",
            "invitation_id",
            "authorization_id",
            "attachment_ids",
            "message_ids",
            "activity_ids",
            self.vendor_b.name,
        ):
            self.assertNotIn(forbidden, rendered)
