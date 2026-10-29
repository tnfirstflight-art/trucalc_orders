from lxml import html

from odoo import fields, http
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .test_bank_order_portal import TestBankOrderPortal
from .test_vendor_order_portal import TestVendorOrderPortal


@tagged("post_install", "-at_install", "trucalc_portal_columns")
class TestBankPortalColumns(TestBankOrderPortal):
    preference_field = "trucalc_bank_portal_columns"

    def _headers(self, user):
        self._login(user)
        response = self.url_open("/my/trucalc/bank/orders?filterby=all")
        self.assertEqual(response.status_code, 200)
        tree = html.fromstring(response.content)
        return [
            " ".join(node.text_content().split())
            for node in tree.xpath("//thead[contains(@class,'o_trucalc_order_list_header')]/tr/th")
        ], tree, response.text

    def _post_columns(self, columns=(), action="apply", **state):
        data = [
            ("csrf_token", self._csrf_token()),
            ("column_action", action),
            ("filterby", state.pop("filterby", "all")),
            ("sort", state.pop("sort", "order_number")),
            ("direction", state.pop("direction", "asc")),
        ]
        data.extend(state.items())
        data.extend(("columns", column) for column in columns)
        return self.url_open(
            "/my/trucalc/bank/columns", data=data, allow_redirects=False,
        )

    def test_role_defaults_and_accessible_menu(self):
        expected = {
            self.bank_admin: [
                "Order Number", "Borrower", "Property Address", "Service Type",
                "Status", "Anticipated Delivery Date", "Fee",
            ],
            self.bank_requestor: [
                "Order Number", "Borrower", "Property Address", "Loan Purpose",
                "Service Type", "Status", "Anticipated Delivery Date",
            ],
            self.bank_viewer: [
                "Order Number", "Borrower", "Property Address", "Service Type",
                "Status", "Anticipated Delivery Date",
            ],
        }
        for user, headers in expected.items():
            with self.subTest(user=user.login):
                actual, tree, _text = self._headers(user)
                self.assertEqual(actual, headers)
                toggles = tree.xpath(
                    "//button[contains(@class,'o_trucalc_column_toggle')]"
                    "[i[contains(@class,'oi-settings-adjust')]]"
                )
                self.assertEqual(len(toggles), 1)
                self.assertEqual(
                    toggles[0].get("aria-label"), "Configure Order list columns",
                )
                required = tree.xpath(
                    "//input[@name='columns'][@value='order_number']"
                )
                self.assertEqual(len(required), 1)
                self.assertIsNotNone(required[0].get("checked"))
                self.assertIsNotNone(required[0].get("disabled"))

    def test_apply_persistence_reset_and_field_independence(self):
        self.bank_admin.sudo().write({
            "trucalc_vendor_portal_columns": ["order_number", "response"],
        })
        self._login(self.bank_admin)
        response = self._post_columns(
            ("loan_number", "unknown", "loan_number"),
            search="Approved Bank", status="accepted", service="evaluation",
            date_field="request", date_from="2026-01-01", date_to="2026-12-31",
        )
        self.assertIn(response.status_code, (302, 303))
        location = response.headers["Location"]
        self.assertTrue(location.startswith("/my/trucalc/bank/orders?"))
        for value in (
            "filterby=all", "search=Approved+Bank", "status=accepted",
            "service=evaluation", "date_field=request", "sort=order_number",
        ):
            self.assertIn(value, location)
        self.bank_admin.invalidate_recordset([
            self.preference_field, "trucalc_vendor_portal_columns",
        ])
        self.assertEqual(
            self.bank_admin.sudo()[self.preference_field],
            ["order_number", "loan_number"],
        )
        self.assertEqual(
            self.bank_admin.sudo().trucalc_vendor_portal_columns,
            ["order_number", "response"],
        )

        self.authenticate(None, None)
        headers, _tree, _text = self._headers(self.bank_admin)
        self.assertEqual(headers, ["Order Number", "Loan Number"])
        self.assertFalse(self.other_bank_user.sudo()[self.preference_field])

        response = self._post_columns(action="reset")
        self.assertIn(response.status_code, (302, 303))
        self.bank_admin.invalidate_recordset([self.preference_field])
        self.assertFalse(self.bank_admin.sudo()[self.preference_field])
        headers, _tree, _text = self._headers(self.bank_admin)
        self.assertIn("Fee", headers)

    def test_route_csrf_role_and_direct_write_boundaries(self):
        self._login(self.bank_admin)
        missing_csrf = self.url_open(
            "/my/trucalc/bank/columns",
            data={"column_action": "reset"},
            allow_redirects=False,
        )
        self.assertEqual(missing_csrf.status_code, 400)
        with self.assertRaises(AccessError):
            self.bank_admin.with_user(self.bank_admin).write({
                self.preference_field: ["order_number", "loan_number"],
            })

        self._login(self.plain_portal)
        denied = self.url_open(
            "/my/trucalc/bank/columns",
            data={"csrf_token": self._csrf_token(), "column_action": "reset"},
            allow_redirects=False,
        )
        self.assertEqual(denied.status_code, 404)

    def test_authoritative_completion_request_date_and_fee(self):
        completion_at = fields.Datetime.to_datetime("2026-10-15 14:30:00")
        self.env.cr.execute(
            "UPDATE trucalc_order SET status='completed', order_date=%s, "
            "current_agreed_fee=%s, fee_currency_id=%s WHERE id=%s",
            ("2026-10-01", 525, self.env.company.currency_id.id, self.order_a.id),
        )
        self.env.cr.execute(
            """
            INSERT INTO trucalc_order_lifecycle_event
                (order_id, stable_order_id, company_id, event_type,
                 from_status, to_status, actor_id, event_at)
            VALUES (%s, %s, %s, 'order_completed',
                    'under_review', 'completed', %s, %s)
            """,
            (
                self.order_a.id, self.order_a.id, self.bank_a.id,
                self.admin.id, completion_at,
            ),
        )
        self.order_a.invalidate_recordset()
        self.bank_admin.sudo().write({self.preference_field: [
            "order_number", "request_date", "completion_date", "fee",
        ]})
        headers, _tree, text = self._headers(self.bank_admin)
        self.assertEqual(
            headers, ["Order Number", "Request Date", "Completion Date", "Fee"],
        )
        self.assertIn("10/01/2026", text)
        self.assertIn("10/15/2026", text)
        self.assertIn("525.00", text)


@tagged("post_install", "-at_install", "trucalc_portal_columns")
class TestVendorPortalColumns(TestVendorOrderPortal):
    preference_field = "trucalc_vendor_portal_columns"

    def _headers(self, user=None):
        self._login(user or self.vendor_user_a)
        response = self.url_open("/my/trucalc/orders?filterby=all")
        self.assertEqual(response.status_code, 200)
        tree = html.fromstring(response.content)
        return [
            " ".join(node.text_content().split())
            for node in tree.xpath("//thead[contains(@class,'o_trucalc_order_list_header')]/tr/th")
        ], tree, response.text

    def _csrf_token(self):
        return http.Request.csrf_token(self)

    def _post_columns(self, columns=(), action="apply", **state):
        data = [
            ("csrf_token", self._csrf_token()),
            ("column_action", action),
            ("filterby", state.pop("filterby", "all")),
            ("sort", state.pop("sort", "order_number")),
            ("direction", state.pop("direction", "asc")),
        ]
        data.extend(state.items())
        data.extend(("columns", column) for column in columns)
        return self.url_open(
            "/my/trucalc/vendor/columns", data=data, allow_redirects=False,
        )

    def test_vendor_defaults_apply_persistence_reset_and_loan_exclusion(self):
        order, invitation, projection = self._authorized_order(
            self.vendor_a, "Column Preference Street",
        )
        expected = [
            "Order Number", "Borrower", "Property Address", "Loan Purpose",
            "Service Type", "Status", "Delivery Due Date", "Response",
        ]
        headers, tree, text = self._headers()
        self.assertEqual(headers, expected)
        self.assertNotIn(order.loan_number, text)
        self.assertFalse(tree.xpath("//input[@name='columns'][@value='loan_number']"))
        self.assertIn("invitation_date", projection.fields_get())
        self.assertIn("delivery_due_date", projection.fields_get())
        self.assertNotIn("loan_number", projection.fields_get())
        self.assertNotIn("loan_number", projection.read()[0])
        self.assertEqual(projection.delivery_due_date, invitation.requested_delivery_date)

        response = self._post_columns((
            "invitation_date", "bid_due_date", "agreed_vendor_fee",
            "delivery_due_date", "loan_number", "response",
        ), search="Column Preference", service="evaluation")
        self.assertIn(response.status_code, (302, 303))
        self.vendor_user_a.invalidate_recordset([self.preference_field])
        self.assertEqual(self.vendor_user_a.sudo()[self.preference_field], [
            "order_number", "invitation_date", "bid_due_date",
            "agreed_vendor_fee", "delivery_due_date", "response",
        ])
        self.assertFalse(self.vendor_user_b.sudo()[self.preference_field])

        self.authenticate(None, None)
        headers, _tree, text = self._headers()
        self.assertEqual(headers, [
            "Order Number", "Invitation Date", "Bid Due Date",
            "Agreed Vendor Fee", "Delivery Due Date", "Response",
        ])
        self.assertNotIn(order.loan_number, text)

        response = self._post_columns(action="reset")
        self.assertIn(response.status_code, (302, 303))
        self.vendor_user_a.invalidate_recordset([self.preference_field])
        self.assertFalse(self.vendor_user_a.sudo()[self.preference_field])

    def test_unified_delivery_date_after_assignment(self):
        order, projection = self._engaged_order(
            self.vendor_a, self.vendor_user_a, "Assigned Delivery Street",
        )
        self.assertEqual(projection.delivery_due_date, order.vendor_delivery_date)
        self.assertEqual(projection.agreed_vendor_fee, order.vendor_fee)
        self.assertNotIn("loan_number", projection.fields_get())

    def test_vendor_route_csrf_and_role_boundaries(self):
        self._login(self.vendor_user_a)
        missing_csrf = self.url_open(
            "/my/trucalc/vendor/columns",
            data={"column_action": "reset"},
            allow_redirects=False,
        )
        self.assertEqual(missing_csrf.status_code, 400)
        with self.assertRaises(AccessError):
            self.vendor_user_a.with_user(self.vendor_user_a).write({
                self.preference_field: ["order_number", "invitation_date"],
            })

        self._login(self.plain_portal_user)
        denied = self.url_open(
            "/my/trucalc/vendor/columns",
            data={"csrf_token": self._csrf_token(), "column_action": "reset"},
            allow_redirects=False,
        )
        self.assertEqual(denied.status_code, 404)
