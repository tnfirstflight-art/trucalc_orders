import base64
from datetime import timedelta
from urllib.parse import urlencode
from unittest.mock import patch

from lxml import html

from odoo import Command, fields
from odoo.tests import tagged

from odoo.addons.trucalc_orders.controllers.portal import TruCalcVendorPortal
from odoo.addons.trucalc_orders.models.bank_invoice import BankInvoice

from .test_bank_order_portal import TestBankOrderPortal


@tagged("post_install", "-at_install", "trucalc_bank_navigation")
class TestBankNavigation(TestBankOrderPortal):
    """Pass B navigation semantics on top of the established Bank authorization."""

    def _set_order(self, order, status, requestor=False):
        self.env.cr.execute(
            "UPDATE trucalc_order SET status = %s, requestor_id = %s WHERE id = %s",
            (status, requestor.id if requestor else self.admin.id, order.id),
        )
        order.invalidate_recordset(["status", "requestor_id"])
        return order

    def _status_order(self, bank, label, status, requestor=False):
        order = self._order(bank, label, "%s Address" % label)
        return self._set_order(order, status, requestor=requestor)

    def _event(self, order, event_type, event_at):
        self.env.cr.execute("""
            INSERT INTO trucalc_order_lifecycle_event
                (order_id, stable_order_id, company_id, event_type,
                 from_status, to_status, actor_id, event_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            order.id, order.id, order.company_id.id, event_type,
            "under_review", "completed", self.admin.id, event_at,
        ))

    def _invoice(self, order, status, issued_at):
        invoice = super(BankInvoice, self.env["trucalc.bank.invoice"].sudo()).create({
            "order_id": order.id,
            "company_id": order.company_id.id,
            "invoice_number": order.order_number,
            "invoice_date": issued_at.date(),
            "due_date": issued_at.date() + timedelta(days=30),
            "amount": 1,
            "currency_id": self.env.company.currency_id.id,
            "status": "issued",
            "issued_by": self.admin.id,
            "issued_at": issued_at,
            "fee_workflow_revision": 0,
            "original_agreed_fee": 1,
            "issuer_company_id": self.env.ref("base.main_company").id,
            "printed_values": {"fixture": True},
            "pdf_data": base64.b64encode(b"%PDF-1.4 fixture"),
            "pdf_filename": "%s.pdf" % order.order_number,
        })
        if status == "paid":
            self.env.cr.execute("""
                UPDATE trucalc_bank_invoice
                   SET status = 'paid', paid_at = %s, paid_by = %s, paid_date = %s
                 WHERE id = %s
            """, (issued_at, self.admin.id, issued_at.date(), invoice.id))
        elif status == "void":
            self.env.cr.execute("""
                UPDATE trucalc_bank_invoice
                   SET status = 'void', voided_at = %s, voided_by = %s,
                       void_reason = 'Test fixture'
                 WHERE id = %s
            """, (issued_at, self.admin.id, invoice.id))
        invoice.invalidate_recordset(["status"])
        return invoice

    def _listing(self, actor, filterby=None):
        self._login(actor)
        suffix = "?filterby=%s" % filterby if filterby else ""
        response = self.url_open("/my/trucalc/bank/orders%s" % suffix)
        self.assertEqual(response.status_code, 200)
        return response, html.fromstring(response.content)

    def test_filter_navigation_default_invalid_pager_and_shell(self):
        for selected in (None, "invalid"):
            response, tree = self._listing(self.bank_admin, selected)
            filters = tree.xpath('//header//nav[@aria-label="Bank Orders filters"]//a')
            self.assertEqual(
                [(node.get("data-filter-key"), node.text_content().strip()) for node in filters],
                [("open", "Open Orders"),
                 ("recent_completed", "Recently Completed"),
                 ("outstanding_invoices", "Outstanding Invoices"),
                 ("all", "All Orders")],
            )
            active = [node for node in filters if node.get("aria-current") == "page"]
            self.assertEqual([node.get("data-filter-key") for node in active], ["open"])
            self.assertTrue(all("btn-primary" in node.get("class", "") for node in active))
            self.assertFalse(tree.xpath('//ol[contains(@class,"breadcrumb")]'))
            self.assertIn("New Request", response.text)
            self.assertTrue(tree.xpath('//header//a[contains(@class,"logo")]/img'))
            self.assertTrue(tree.xpath('//header//a[@id="o_logout"]'))
            for node in filters:
                self.assertNotIn("/page/", node.get("href"))

        for selected in (
            "open", "recent_completed", "outstanding_invoices", "all",
        ):
            _, tree = self._listing(self.bank_admin, selected)
            active = tree.xpath(
                '//header//nav[@aria-label="Bank Orders filters"]'
                '//a[@aria-current="page"]/@data-filter-key'
            )
            self.assertEqual(active, [selected])

        for number in range(4):
            self._status_order(self.bank_a, "Pager Open %s" % number, "new")
        with patch.object(TruCalcVendorPortal, "_portal_order_page_size", 2):
            _, tree = self._listing(self.bank_admin, "open")
        page_two = tree.xpath('//a[contains(@href,"/page/2")]/@href')
        self.assertTrue(page_two)
        self.assertTrue(any("filterby=open" in href for href in page_two))
        self.assertEqual(len(tree.xpath(
            '//div[contains(concat(" ", normalize-space(@class), " "),'
            ' " o_portal_pager ")]'
        )), 1)

    def test_pass_a_pagination_search_sort_filters_and_invalid_input(self):
        for number in range(25):
            self._status_order(
                self.bank_a, "Pass A Page %02d" % number, "new",
            )
        query = {
            "filterby": "open", "search": "Pass A Page",
            "sort": "borrower", "direction": "asc",
        }
        self._login(self.bank_admin)
        first = self.url_open(
            "/my/trucalc/bank/orders?%s" % urlencode(query)
        )
        first_tree = html.fromstring(first.content)
        self.assertEqual(len(first_tree.xpath("//table/tbody/tr")), 20)
        toggle = first_tree.xpath("//button[contains(@class,'o_trucalc_filter_toggle')]")
        self.assertEqual(len(toggle), 1)
        self.assertEqual(toggle[0].get("aria-expanded"), "false")
        self.assertIn("Active", toggle[0].text_content())
        panel = first_tree.get_element_by_id("trucalc-order-filter-panel")
        self.assertIn("collapse", panel.get("class", ""))
        self.assertNotIn("show", panel.get("class", "").split())
        self.assertTrue(first_tree.xpath(
            "//thead[contains(@class,'o_trucalc_order_list_header')]"
        ))
        self.assertTrue(first_tree.xpath(
            "//div[@id='trucalc-order-filter-panel']//a[normalize-space()='Clear']"
        ))
        self.assertIn(
            "Showing 1–20 of 25", " ".join(first_tree.text_content().split()),
        )
        category_hrefs = first_tree.xpath(
            '//nav[@aria-label="Bank Orders filters"]//a/@href'
        )
        self.assertTrue(category_hrefs)
        self.assertTrue(all("search=Pass+A+Page" in href for href in category_hrefs))
        page_two = sorted(set(
            first_tree.xpath('//a[contains(@href,"/page/2")]/@href')
        ))
        self.assertEqual(len(page_two), 1)
        for value in ("filterby=open", "search=Pass+A+Page", "sort=borrower"):
            self.assertIn(value, page_two[0])

        second = self.url_open(page_two[0])
        second_tree = html.fromstring(second.content)
        self.assertEqual(len(second_tree.xpath("//table/tbody/tr")), 5)
        self.assertIn(
            "Showing 21–25 of 25", " ".join(second_tree.text_content().split()),
        )

        alpha = self._status_order(
            self.bank_a, "Pass A Sort Alpha", "new",
        )
        zulu = self._status_order(
            self.bank_a, "Pass A Sort Zulu", "new",
        )
        foreign = self._status_order(
            self.bank_b, "Pass A Sort Foreign", "new",
        )
        filter_query = {
            "filterby": "open", "search": "Pass A Sort",
            "status": "new", "service": "evaluation",
            "date_field": "request",
            "date_from": fields.Date.to_string(fields.Date.today()),
            "date_to": fields.Date.to_string(fields.Date.today()),
            "sort": "borrower", "direction": "asc",
        }
        ascending = html.fromstring(self.url_open(
            "/my/trucalc/bank/orders?%s" % urlencode(filter_query)
        ).content)
        ascending_text = ascending.text_content()
        self.assertIn(alpha.order_number, ascending_text)
        self.assertIn(zulu.order_number, ascending_text)
        self.assertNotIn(foreign.order_number, ascending_text)
        borrowers = [
            value.strip() for value in ascending.xpath("//table/tbody/tr/td[2]//text()")
            if value.strip()
        ]
        self.assertEqual(borrowers, sorted(borrowers))

        filter_query["direction"] = "desc"
        descending = html.fromstring(self.url_open(
            "/my/trucalc/bank/orders?%s" % urlencode(filter_query)
        ).content)
        borrowers_desc = [
            value.strip() for value in descending.xpath("//table/tbody/tr/td[2]//text()")
            if value.strip()
        ]
        self.assertEqual(borrowers_desc, sorted(borrowers_desc, reverse=True))

        order_search = self.url_open(
            "/my/trucalc/bank/orders?%s" % urlencode({
                "filterby": "open", "search": alpha.order_number,
            })
        ).text
        self.assertIn(alpha.order_number, order_search)
        self.assertNotIn(zulu.order_number, order_search)

        invalid = self.url_open(
            "/my/trucalc/bank/orders?filterby=open&status=company_id"
            "&sort=company_id&direction=drop&date_field=unknown"
        )
        invalid_tree = html.fromstring(invalid.content)
        self.assertIn("list filters were invalid", invalid.text)
        self.assertFalse(invalid_tree.xpath("//table/tbody/tr"))
        self.assertNotIn(foreign.order_number, invalid.text)

    def test_order_list_ux_browser_responsive_and_accessible(self):
        self._status_order(self.bank_a, "Responsive Active", "new")
        code = """
            (() => {
                const toggle = document.querySelector('.o_trucalc_filter_toggle');
                const panel = document.querySelector('#trucalc-order-filter-panel');
                const header = document.querySelector('thead.o_trucalc_order_list_header');
                const link = header?.querySelector('.o_trucalc_sort_link');
                const icon = header?.querySelector('.o_trucalc_sort_link .fa');
                if (!toggle || !panel || !header || !link || !icon) throw Error('UX controls missing');
                if (panel.classList.contains('show')) throw Error('Filters initially open');
                if (toggle.getAttribute('aria-expanded') !== 'false') throw Error('Initial collapse state');
                if (getComputedStyle(header).backgroundColor !== 'rgb(2, 47, 91)') throw Error('Header background');
                if (getComputedStyle(link).color !== 'rgb(255, 255, 255)') throw Error('Header link color');
                if (getComputedStyle(icon).color !== 'rgb(255, 255, 255)') throw Error('Sort icon color');
                link.focus();
                const focusStyle = getComputedStyle(link);
                if (focusStyle.outlineStyle === 'none' || focusStyle.outlineWidth === '0px') throw Error('Focus indicator');
                if (document.documentElement.scrollWidth > document.documentElement.clientWidth + 1) throw Error('Page overflow');
                toggle.click();
                setTimeout(() => {
                    if (!panel.classList.contains('show')) throw Error('Filters did not open');
                    if (toggle.getAttribute('aria-expanded') !== 'true') throw Error('Expanded collapse state');
                    console.log('test successful');
                }, 400);
            })();
        """
        for size in ("1366x768", "768x1024", "390x844"):
            with self.subTest(size=size):
                self.browser_size = size
                self.browser_js(
                    "/my/trucalc/bank/orders?sort=order_number&direction=asc",
                    code,
                    login=self.bank_admin.login,
                    ready=(
                        "document.readyState === 'complete' && "
                        "document.querySelector('.o_trucalc_filter_toggle')?."
                        "dataset.trucalcFilterReady === 'true'"
                    ),
                    timeout=90,
                )

    def test_open_all_and_role_scoping(self):
        active = self._status_order(self.bank_a, "Pass B Active", "accepted")
        completed = self._status_order(self.bank_a, "Pass B Completed", "completed")
        declined = self._status_order(self.bank_a, "Pass B Declined", "declined")
        cancelled = self._status_order(self.bank_a, "Pass B Cancelled", "cancelled")
        own_draft = self._status_order(
            self.bank_a, "Pass B Own Draft", "draft", self.bank_requestor,
        )
        other_draft = self._status_order(
            self.bank_a, "Pass B Other Draft", "draft", self.bank_requestor_b,
        )
        foreign = self._status_order(self.bank_b, "Pass B Foreign", "accepted")

        _, admin_open = self._listing(self.bank_admin, "open")
        admin_text = admin_open.text_content()
        for order in (active, own_draft, other_draft):
            self.assertIn(order.order_number, admin_text)
        for order in (completed, declined, cancelled, foreign):
            self.assertNotIn(order.order_number, admin_text)

        _, requester_open = self._listing(self.bank_requestor, "open")
        requester_text = requester_open.text_content()
        self.assertIn(active.order_number, requester_text)
        self.assertIn(own_draft.order_number, requester_text)
        self.assertNotIn(other_draft.order_number, requester_text)

        _, viewer_open = self._listing(self.bank_viewer, "open")
        viewer_text = viewer_open.text_content()
        self.assertIn(active.order_number, viewer_text)
        self.assertNotIn(own_draft.order_number, viewer_text)
        self.assertNotIn(other_draft.order_number, viewer_text)

        _, all_orders = self._listing(self.bank_admin, "all")
        all_text = all_orders.text_content()
        for order in (active, completed, declined, cancelled):
            self.assertIn(order.order_number, all_text)
        for order in (own_draft, other_draft, foreign):
            self.assertNotIn(order.order_number, all_text)

    def test_recent_completion_uses_same_event_row_and_exact_window(self):
        now = fields.Datetime.to_datetime("2026-09-01 12:00:00")
        recent = self._status_order(self.bank_a, "Pass B Recent", "completed")
        boundary = self._status_order(self.bank_a, "Pass B Boundary", "completed")
        old = self._status_order(self.bank_a, "Pass B Old", "completed")
        future = self._status_order(self.bank_a, "Pass B Future", "completed")
        missing = self._status_order(self.bank_a, "Pass B Missing", "completed")
        mismatch = self._status_order(self.bank_a, "Pass B Mismatch", "completed")
        foreign = self._status_order(self.bank_b, "Pass B Foreign Recent", "completed")
        self._event(recent, "order_completed", now - timedelta(days=1))
        self._event(boundary, "order_completed", now - timedelta(days=30))
        self._event(old, "order_completed", now - timedelta(days=30, seconds=1))
        self._event(future, "order_completed", now + timedelta(seconds=1))
        self._event(mismatch, "order_completed", now - timedelta(days=31))
        self._event(mismatch, "review_accepted", now - timedelta(days=1))
        self._event(foreign, "order_completed", now - timedelta(days=1))

        with patch.object(TruCalcVendorPortal, "_bank_filter_now", return_value=now):
            _, tree = self._listing(self.bank_admin, "recent_completed")
        text = tree.text_content()
        for order in (recent, boundary):
            self.assertIn(order.order_number, text)
        for order in (old, future, missing, mismatch, foreign):
            self.assertNotIn(order.order_number, text)

    def test_outstanding_invoice_detail_cleanup_and_empty_messages(self):
        now = fields.Datetime.to_datetime("2026-09-01 12:00:00")
        issued = self._status_order(self.bank_a, "Pass B Issued", "completed")
        paid = self._status_order(self.bank_a, "Pass B Paid", "completed")
        void = self._status_order(self.bank_a, "Pass B Void", "completed")
        no_invoice = self._status_order(self.bank_a, "Pass B No Invoice", "completed")
        foreign = self._status_order(self.bank_b, "Pass B Foreign Invoice", "completed")
        self._invoice(issued, "issued", now)
        self._invoice(paid, "paid", now)
        self._invoice(void, "void", now)
        self._invoice(foreign, "issued", now)

        _, tree = self._listing(self.bank_admin, "outstanding_invoices")
        text = tree.text_content()
        self.assertIn(issued.order_number, text)
        for order in (paid, void, no_invoice, foreign):
            self.assertNotIn(order.order_number, text)

        detail = self.url_open(
            "/my/trucalc/bank/orders/%s/documents" % self.order_a.order_number
        )
        detail_tree = html.fromstring(detail.content)
        self.assertFalse(detail_tree.xpath('//ol[contains(@class,"breadcrumb")]'))
        self.assertFalse(detail_tree.xpath('//h2[contains(.,"TruCalc Request")]'))
        self.assertTrue(detail_tree.xpath('//a[normalize-space()="Back to My TruCalc Requests"]'))
        self.assertIn("Request Details", detail.text)
        self.assertIn("Documents", detail.text)

        draft = self._status_order(
            self.bank_a, "Pass B Draft Identifier", "draft", self.bank_requestor,
        )
        draft_page = self.url_open(
            "/my/trucalc/bank/orders/%s/documents" % draft.order_number
        )
        self.assertIn("Order Number:", draft_page.text)
        self.assertIn(draft.order_number, draft_page.text)

        empty_bank = self.env["res.company"].with_context(trucalc_test_bank_fixture=True).create({"name": "Pass B Empty Bank", "trucalc_is_bank": True, "trucalc_bank_active": True})
        empty_user = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Pass B Empty Admin",
            "login": "pass-b-empty-admin",
            "password": self.password,
            "group_ids": [Command.set([
                self.env.ref("trucalc_orders.group_bank_admin").id,
            ])],
            "trucalc_bank_company_id": empty_bank.id,
            "company_id": empty_bank.id,
            "company_ids": [Command.set(empty_bank.ids)],
        })
        messages = {
            "open": "No open TruCalc requests.",
            "recent_completed": "No requests were completed in the last 30 days.",
            "outstanding_invoices": "No outstanding invoices.",
            "all": "No TruCalc requests found.",
        }
        for filterby, message in messages.items():
            response, _tree = self._listing(empty_user, filterby)
            self.assertIn(message, response.text)
