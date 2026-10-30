from datetime import datetime, timedelta
from unittest.mock import patch

from lxml import html

from odoo import Command, fields, http
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .test_bank_order_portal import TestBankOrderPortal
from .test_vendor_order_portal import TestVendorOrderPortal


@tagged("post_install", "-at_install", "trucalc_portal_columns")
class TestBankPortalColumns(TestBankOrderPortal):
    preference_field = "trucalc_bank_portal_columns"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.delivery_vendor_a = cls.env["trucalc.vendor"].create({
            "name": "4C2 Delivery Vendor A", "vendor_type": "appraiser",
        })
        cls.delivery_vendor_b = cls.env["trucalc.vendor"].create({
            "name": "4C2 Delivery Vendor B", "vendor_type": "appraiser",
        })
        cls.env["trucalc.vendor.fee"].create([
            {
                "vendor_id": cls.delivery_vendor_a.id,
                "service_type": "evaluation", "fee": 500,
            },
            {
                "vendor_id": cls.delivery_vendor_b.id,
                "service_type": "evaluation", "fee": 600,
            },
        ])
        cls.delivery_vendor_user_a = cls._delivery_vendor_user(
            "a", cls.delivery_vendor_a,
        )
        cls.delivery_vendor_user_b = cls._delivery_vendor_user(
            "b", cls.delivery_vendor_b,
        )

    @classmethod
    def _delivery_vendor_user(cls, suffix, vendor):
        return cls.env["res.users"].with_context(no_reset_password=True).create({
            "name": "4C2 Delivery Vendor %s" % suffix.upper(),
            "login": "4c2-delivery-vendor-%s" % suffix,
            "password": cls.password,
            "group_ids": [Command.set([
                cls.env.ref("trucalc_orders.group_vendor_portal").id,
            ])],
            "trucalc_vendor_id": vendor.id,
        })

    def _headers(self, user, query="filterby=all"):
        self._login(user)
        response = self.url_open("/my/trucalc/bank/orders?%s" % query)
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

    def _delivery_order(self, label, requested_due):
        return self.env["trucalc.order"].with_user(self.admin).create({
            "borrower": "%s Borrower" % label,
            "property_address": "%s Delivery Street" % label,
            "company_id": self.bank_a.id,
            "service_type": "evaluation",
            "due_date": requested_due,
        })

    def _engage_delivery(self, order, vendor, vendor_user, delivery_date):
        if order.status == "new":
            order.with_user(self.admin).action_accept_request()
            order.with_user(self.admin).action_bid_requested()
            invitation = self.env["trucalc.bid.invitation"].with_user(
                self.admin
            ).create({
                "order_id": order.id,
                "vendor_id": vendor.id,
                "response_deadline": fields.Datetime.add(
                    fields.Datetime.now(), days=2,
                ),
            })
        else:
            order.with_user(self.admin).action_request_vendor_bids(
                vendor, fields.Datetime.add(fields.Datetime.now(), days=2),
            )
            invitation = order.invitation_ids.filtered(
                lambda item: item.round_number == order.bidding_round
                and item.vendor_id == vendor
            )
        if delivery_date == invitation.requested_delivery_date:
            bid = invitation.with_user(vendor_user).action_vendor_submit_response(
                "standard_terms_accepted",
            )
        else:
            bid = invitation.with_user(vendor_user).action_vendor_submit_response(
                "delivery_date_counter", proposed_delivery_date=delivery_date,
            )
        bid.with_user(self.admin)._action_confirm_engagement()
        self.env.flush_all()
        order.invalidate_recordset()
        engagement = order.sudo().engagement_ids.filtered("active")
        return invitation, engagement

    def test_role_defaults_and_accessible_menu(self):
        expected = {
            self.bank_admin: [
                "Order Number", "Borrower", "Property Address", "Service Type",
                "Status", "Est. Delivery", "Fee",
            ],
            self.bank_requestor: [
                "Order Number", "Borrower", "Property Address", "Loan Purpose",
                "Service Type", "Status", "Est. Delivery",
            ],
            self.bank_viewer: [
                "Order Number", "Borrower", "Property Address", "Service Type",
                "Status", "Est. Delivery",
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
                city = tree.xpath(
                    "//input[@name='columns'][@value='city']"
                )
                self.assertEqual(len(city), 1)
                self.assertIsNone(city[0].get("checked"))
                order_date = tree.xpath(
                    "//input[@name='columns'][@value='request_date']"
                )[0]
                requested_due = tree.xpath(
                    "//input[@name='columns'][@value='anticipated_delivery_date']"
                )[0]
                estimated = tree.xpath(
                    "//input[@name='columns'][@value='estimated_delivery_date']"
                )[0]
                self.assertIsNone(order_date.get("checked"))
                self.assertIsNone(requested_due.get("checked"))
                self.assertIsNotNone(estimated.get("checked"))

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
        self.assertIn("Est. Delivery", headers)
        self.assertNotIn("Requested Due", headers)
        self.assertNotIn("Order Date", headers)

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
            "UPDATE trucalc_order SET status='completed', order_date=%s, city=%s, "
            "current_agreed_fee=%s, fee_currency_id=%s WHERE id=%s",
            (
                "2026-10-01", "Purpose-Built City", 525,
                self.env.company.currency_id.id, self.order_a.id,
            ),
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
            "order_number", "city", "request_date",
            "anticipated_delivery_date", "estimated_delivery_date",
            "completion_date", "fee",
        ]})
        headers, _tree, text = self._headers(self.bank_admin)
        self.assertEqual(
            headers, [
                "Order Number", "City", "Order Date", "Requested Due",
                "Est. Delivery", "Completion Date", "Fee",
            ],
        )
        self.assertIn("Purpose-Built City", text)
        self.assertIn("10/01/2026", text)
        self.assertIn("10/15/2026", text)
        self.assertIn("525.00", text)

    def test_authoritative_delivery_dates_warning_changes_and_reassignment(self):
        today = fields.Date.today()
        requested = fields.Date.add(today, days=14)
        earlier = fields.Date.add(today, days=10)
        later = fields.Date.add(today, days=18)
        changed = fields.Date.add(today, days=21)

        draft = self.env["trucalc.order"].with_user(
            self.bank_admin
        )._create_bank_draft(
            self._complete_draft_values(
                borrower="Delivery Draft", due_date=fields.Date.to_string(requested),
            ),
            self.bank_admin,
        )
        awaiting = self._delivery_order("Awaiting", requested)
        matching = self._delivery_order("Matching", requested)
        later_order = self._delivery_order("Later", requested)
        earlier_order = self._delivery_order("Earlier", requested)
        change_order = self._delivery_order("Changed", requested)

        _invitation, matching_engagement = self._engage_delivery(
            matching, self.delivery_vendor_a,
            self.delivery_vendor_user_a, requested,
        )
        matching_engagement.with_user(
            self.delivery_vendor_user_a
        ).action_vendor_accept()
        _invitation, later_engagement = self._engage_delivery(
            later_order, self.delivery_vendor_a,
            self.delivery_vendor_user_a, later,
        )
        later_engagement.with_user(
            self.delivery_vendor_user_a
        ).action_vendor_accept()
        _invitation, earlier_engagement = self._engage_delivery(
            earlier_order, self.delivery_vendor_a,
            self.delivery_vendor_user_a, earlier,
        )
        earlier_engagement.with_user(
            self.delivery_vendor_user_a
        ).action_vendor_accept()
        _invitation, change_engagement = self._engage_delivery(
            change_order, self.delivery_vendor_a,
            self.delivery_vendor_user_a, requested,
        )

        self.bank_admin.sudo().write({self.preference_field: [
            "order_number", "request_date", "anticipated_delivery_date",
            "estimated_delivery_date",
        ]})

        def rows_by_order():
            headers, tree, _text = self._headers(
                self.bank_admin, "filterby=open&search=Delivery",
            )
            self.assertEqual(headers, [
                "Order Number", "Order Date", "Requested Due", "Est. Delivery",
            ])
            return {
                int(row.get("data-order-id")): row
                for row in tree.xpath("//table/tbody/tr[@data-order-id]")
            }

        def cell_text(row, column):
            return row.xpath("./td[@data-column='%s']" % column)[0].text_content().strip()

        rows = rows_by_order()
        self.assertEqual(cell_text(rows[draft.id], "order_date"), "")
        self.assertEqual(cell_text(rows[awaiting.id], "estimated_delivery"), "")
        self.assertEqual(
            cell_text(rows[matching.id], "estimated_delivery"),
            requested.strftime("%m/%d/%Y"),
        )
        self.assertFalse(rows[matching.id].xpath(
            ".//*[contains(@class,'o_trucalc_delivery_warning')]"
        ))
        self.assertEqual(
            cell_text(rows[later_order.id], "estimated_delivery"),
            later.strftime("%m/%d/%Y"),
        )
        warning = rows[later_order.id].xpath(
            ".//*[contains(@class,'o_trucalc_delivery_warning')]"
        )
        self.assertEqual(len(warning), 1)
        explanation = "Estimated delivery exceeds the Bank's requested date."
        self.assertEqual(warning[0].get("aria-label"), explanation)
        self.assertEqual(warning[0].get("title"), explanation)
        self.assertEqual(
            cell_text(rows[earlier_order.id], "estimated_delivery"),
            earlier.strftime("%m/%d/%Y"),
        )
        self.assertFalse(rows[earlier_order.id].xpath(
            ".//*[contains(@class,'o_trucalc_delivery_warning')]"
        ))

        change_projection = self.env["trucalc.vendor.order"].with_user(
            self.delivery_vendor_user_a
        ).search([("order_number", "=", change_order.order_number)])
        change_projection.action_vendor_request_delivery_change(
            changed, "Need additional inspection time",
        )
        self.env.flush_all()
        rows = rows_by_order()
        self.assertEqual(
            cell_text(rows[change_order.id], "estimated_delivery"), "",
        )
        change_engagement.with_user(self.admin).action_approve_delivery_change()
        self.env.flush_all()
        rows = rows_by_order()
        self.assertEqual(
            cell_text(rows[change_order.id], "estimated_delivery"),
            changed.strftime("%m/%d/%Y"),
        )
        self.assertTrue(rows[change_order.id].xpath(
            ".//*[contains(@class,'o_trucalc_delivery_warning')]"
        ))

        later_order.with_user(self.admin).action_reopen_bidding()
        self.env.flush_all()
        later_order.invalidate_recordset()
        rows = rows_by_order()
        self.assertEqual(
            cell_text(rows[later_order.id], "estimated_delivery"), "",
        )
        reassigned = fields.Date.add(today, days=12)
        _invitation, current_engagement = self._engage_delivery(
            later_order, self.delivery_vendor_b,
            self.delivery_vendor_user_b, reassigned,
        )
        current_engagement.with_user(
            self.delivery_vendor_user_b
        ).action_vendor_accept()
        self.env.flush_all()
        rows = rows_by_order()
        self.assertEqual(
            cell_text(rows[later_order.id], "estimated_delivery"),
            reassigned.strftime("%m/%d/%Y"),
        )
        self.assertFalse(rows[later_order.id].xpath(
            ".//*[contains(@class,'o_trucalc_delivery_warning')]"
        ))

    def test_delivery_columns_browser_responsive(self):
        requested = fields.Date.add(fields.Date.today(), days=14)
        later = fields.Date.add(requested, days=4)
        matching_order = self._delivery_order("Browser Matching", requested)
        later_order = self._delivery_order("Browser Later", requested)
        for order, delivery_date in (
            (matching_order, requested), (later_order, later),
        ):
            _invitation, engagement = self._engage_delivery(
                order, self.delivery_vendor_a,
                self.delivery_vendor_user_a, delivery_date,
            )
            engagement.with_user(
                self.delivery_vendor_user_a
            ).action_vendor_accept()
        self.bank_admin.sudo().write({self.preference_field: [
            "order_number", "request_date", "anticipated_delivery_date",
            "estimated_delivery_date",
        ]})
        code = """
            (() => {
                const headers = [...document.querySelectorAll(
                    '.o_trucalc_order_list_header th'
                )].map(node => node.textContent.trim());
                const expected = ['Order Number', 'Order Date', 'Requested Due', 'Est. Delivery'];
                if (headers.join('|') !== expected.join('|')) throw Error(`Headers: ${headers}`);
                const matching = document.querySelector('tr[data-order-id="%s"]');
                const later = document.querySelector('tr[data-order-id="%s"]');
                if (!matching || !later) throw Error('Delivery fixtures missing');
                if (matching.querySelector('.o_trucalc_delivery_warning')) throw Error('False warning');
                const warning = later.querySelector('.o_trucalc_delivery_warning');
                const explanation = "Estimated delivery exceeds the Bank's requested date.";
                if (!warning || warning.getAttribute('aria-label') !== explanation
                    || warning.getAttribute('title') !== explanation) {
                    throw Error('Late-delivery warning is not accessible');
                }
                if (document.documentElement.scrollWidth > innerWidth + 1) {
                    throw Error(`Page overflow ${document.documentElement.scrollWidth}/${innerWidth}`);
                }
                const tableWrap = document.querySelector('.table-responsive');
                if (!tableWrap) throw Error('Responsive table wrapper missing');
                console.log('test successful');
            })();
        """ % (matching_order.id, later_order.id)
        for size in ("1366x768", "1280x720", "1024x768", "768x1024", "390x844"):
            with self.subTest(size=size):
                self.browser_size = size
                self.browser_js(
                    "/my/trucalc/bank/orders?filterby=open&search=Browser",
                    code, login=self.bank_admin.login,
                    ready="!!document.querySelector('.o_trucalc_order_list_header')",
                    timeout=90,
                )


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
            "Service Type", "Status", "Due Date", "Response",
        ]
        headers, tree, text = self._headers()
        self.assertEqual(headers, expected)
        self.assertNotIn(order.loan_number, text)
        self.assertFalse(tree.xpath("//input[@name='columns'][@value='loan_number']"))
        award = tree.xpath("//input[@name='columns'][@value='award_date']")
        self.assertEqual(len(award), 1)
        self.assertIsNone(award[0].get("checked"))
        menu_labels = [
            " ".join(node.text_content().split())
            for node in tree.xpath(
                "//div[contains(@class,'o_trucalc_column_choices')]//label"
            )
        ]
        self.assertIn("Award Date", menu_labels)
        self.assertIn("Vendor Fee", menu_labels)
        self.assertIn("Due Date", menu_labels)
        self.assertIn("invitation_date", projection.fields_get())
        self.assertIn("award_date", projection.fields_get())
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
            "Vendor Fee", "Due Date", "Response",
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

    def test_local_date_only_authoritative_award_and_reassignment(self):
        self.vendor_user_a.sudo().write({
            "tz": "America/Los_Angeles",
            self.preference_field: [
                "order_number", "invitation_date", "award_date",
            ],
        })
        order, invitation, projection = self._authorized_order(
            self.vendor_a, "Award Date Street",
        )
        invitation_at = datetime(2026, 10, 16, 0, 30)
        self.env.cr.execute(
            "UPDATE trucalc_bid_invitation SET invited_at=%s WHERE id=%s",
            (invitation_at, invitation.id),
        )
        self.env["trucalc.vendor.order"].invalidate_model()

        headers, tree, _text = self._headers()
        self.assertEqual(headers, ["Order Number", "Invitation Date", "Award Date"])
        row = tree.xpath(
            "//a[contains(@href, '%s')]/ancestor::tr[1]" % order.order_number
        )[0]
        self.assertEqual(
            row.xpath("./td[@data-column='invitation_date']")[0].text_content().strip(),
            "10/15/2026",
        )
        self.assertEqual(
            row.xpath("./td[@data-column='award_date']")[0].text_content().strip(),
            "",
        )

        first_award = datetime(2026, 10, 17, 1, 30)
        bid = invitation.with_user(
            self.vendor_user_a
        ).action_vendor_submit_response("standard_terms_accepted")
        with patch.object(fields.Datetime, "now", return_value=first_award):
            bid.with_user(self.admin)._action_confirm_engagement()
        self.env.flush_all()
        self.env["trucalc.vendor.order"].invalidate_model()
        first_engagement = order.sudo().engagement_ids.filtered("active")
        first_projection = self.env["trucalc.vendor.order"].with_user(
            self.vendor_user_a
        ).search([("order_number", "=", order.order_number)])
        self.assertEqual(first_projection.award_date, first_engagement.engaged_at)
        _headers, tree, _text = self._headers()
        row = tree.xpath(
            "//a[contains(@href, '%s')]/ancestor::tr[1]" % order.order_number
        )[0]
        self.assertEqual(
            row.xpath("./td[@data-column='award_date']")[0].text_content().strip(),
            "10/16/2026",
        )

        order.with_user(self.admin).action_reopen_bidding()
        self.env.flush_all()
        order.invalidate_recordset()
        first_engagement.invalidate_recordset(["active", "closed_at"])
        self.assertFalse(first_engagement.active)
        order.with_user(self.admin).action_request_vendor_bids(
            self.vendor_b, fields.Datetime.now() + timedelta(days=2),
        )
        second_invitation = order.invitation_ids.filtered(
            lambda item: item.round_number == order.bidding_round
            and item.vendor_id == self.vendor_b
        )
        second_bid = second_invitation.with_user(
            self.vendor_user_b
        ).action_vendor_submit_response("standard_terms_accepted")
        second_award = datetime(2026, 10, 19, 1, 30)
        with patch.object(fields.Datetime, "now", return_value=second_award):
            second_bid.with_user(self.admin)._action_confirm_engagement()
        self.env.flush_all()
        self.env["trucalc.vendor.order"].invalidate_model()
        active_engagement = order.sudo().engagement_ids.filtered("active")
        self.assertNotEqual(active_engagement, first_engagement)
        self.assertFalse(first_engagement.active)

        self.vendor_user_b.sudo().write({
            "tz": "America/Los_Angeles",
            self.preference_field: ["order_number", "award_date"],
        })
        second_projection = self.env["trucalc.vendor.order"].with_user(
            self.vendor_user_b
        ).search([("order_number", "=", order.order_number)])
        self.assertEqual(second_projection.award_date, active_engagement.engaged_at)
        headers, tree, _text = self._headers(self.vendor_user_b)
        self.assertEqual(headers, ["Order Number", "Award Date"])
        row = tree.xpath(
            "//a[contains(@href, '%s')]/ancestor::tr[1]" % order.order_number
        )[0]
        self.assertEqual(
            row.xpath("./td[@data-column='award_date']")[0].text_content().strip(),
            "10/18/2026",
        )
        _old_vendor_headers, old_vendor_tree, _text = self._headers(
            self.vendor_user_a,
        )
        self.assertFalse(old_vendor_tree.xpath(
            "//a[contains(@href, '%s')]" % order.order_number
        ))

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
