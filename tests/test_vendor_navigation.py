from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode
from unittest.mock import patch

from lxml import html

from odoo import fields
from odoo.tests import tagged

from odoo.addons.trucalc_orders.controllers.portal import TruCalcVendorPortal

from .test_vendor_order_portal import TestVendorOrderPortal


@tagged("post_install", "-at_install", "trucalc_vendor_navigation")
class TestVendorNavigation(TestVendorOrderPortal):
    """Pass B navigation over the existing authorized Vendor projection."""

    def _set_status(self, order, status):
        self.env.cr.execute(
            "UPDATE trucalc_order SET status = %s WHERE id = %s",
            (status, order.id),
        )
        order.invalidate_recordset(["status"])
        self.env["trucalc.vendor.order"].invalidate_model()
        return order

    def _assignment_order(self, label, status="engaged"):
        order, projection = self._engaged_order(
            self.vendor_a, self.vendor_user_a, "%s Address" % label,
        )
        return self._set_status(order, status), projection

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

    def _listing(self, filterby=None, page=None):
        self._login(self.vendor_user_a)
        path = "/my/trucalc/orders"
        if page:
            path += "/page/%s" % page
        if filterby is not None:
            path += "?filterby=%s" % filterby
        response = self.url_open(path)
        self.assertEqual(response.status_code, 200)
        return response, html.fromstring(response.content)

    def test_filter_navigation_default_invalid_pager_and_shell(self):
        for selected in (None, "invalid"):
            _response, tree = self._listing(selected)
            filters = tree.xpath(
                '//header//nav[@aria-label="Vendor Orders filters"]//a'
            )
            self.assertEqual(
                [(node.get("data-filter-key"), node.text_content().strip())
                 for node in filters],
                [("active", "Active Orders"), ("submitted", "Submitted"),
                 ("recent_completed", "Recently Completed"),
                 ("all", "All Orders")],
            )
            current = [
                node for node in filters if node.get("aria-current") == "page"
            ]
            self.assertEqual(
                [node.get("data-filter-key") for node in current], ["active"],
            )
            self.assertIn("btn-primary", current[0].get("class", ""))
            self.assertTrue(all(
                "btn-outline-primary" in node.get("class", "")
                for node in filters if node not in current
            ))
            self.assertTrue(all("/page/" not in node.get("href") for node in filters))
            self.assertFalse(tree.xpath('//nav[contains(@class,"o_trucalc_bank_filters")]'))

        for selected in ("active", "submitted", "recent_completed", "all"):
            _response, tree = self._listing(selected)
            self.assertEqual(tree.xpath(
                '//header//nav[@aria-label="Vendor Orders filters"]'
                '//a[@aria-current="page"]/@data-filter-key'
            ), [selected])

        for number in range(4):
            self._authorized_order(self.vendor_a, "Pager Active %s" % number)
        with patch.object(TruCalcVendorPortal, "_portal_order_page_size", 2):
            _response, tree = self._listing("active")
        page_two = tree.xpath('//a[contains(@href,"/page/2")]/@href')
        self.assertTrue(page_two)
        self.assertTrue(any("filterby=active" in href for href in page_two))
        self.assertEqual(len(tree.xpath(
            '//div[contains(concat(" ", normalize-space(@class), " "),'
            ' " o_portal_pager ")]'
        )), 1)

        styles = (Path(__file__).resolve().parents[1] /
                  "static/src/scss/vendor_portal.scss").read_text()
        self.assertIn(".o_trucalc_vendor_filters", styles)
        self.assertIn("flex-wrap: wrap", styles)
        self.assertNotIn("height:", styles)

    def test_pass_a_pagination_search_sort_filters_and_bounded_query(self):
        fixtures = []
        for number in range(25):
            fixtures.append(self._authorized_order(
                self.vendor_a, "Pass A Vendor Page %02d" % number,
            )[0])
        foreign = self._authorized_order(
            self.vendor_b, "Pass A Vendor Page Foreign",
        )[0]

        projection_type = type(self.env["trucalc.vendor.order"])
        original_search = projection_type.search
        projection_searches = []

        def audited_search(recordset, domain, *args, **kwargs):
            projection_searches.append((domain, kwargs.get("limit")))
            return original_search(recordset, domain, *args, **kwargs)

        query = {
            "filterby": "active", "search": "Pass A Vendor Page",
            "sort": "borrower", "direction": "asc",
        }
        self._login(self.vendor_user_a)
        with patch.object(projection_type, "search", new=audited_search):
            first = self.url_open(
                "/my/trucalc/orders?%s" % urlencode(query)
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
        self.assertEqual([limit for _domain, limit in projection_searches], [20])
        category_hrefs = first_tree.xpath(
            '//nav[@aria-label="Vendor Orders filters"]//a/@href'
        )
        self.assertTrue(category_hrefs)
        self.assertTrue(all(
            "search=Pass+A+Vendor+Page" in href for href in category_hrefs
        ))
        page_two = sorted(set(
            first_tree.xpath('//a[contains(@href,"/page/2")]/@href')
        ))
        self.assertEqual(len(page_two), 1)
        for value in (
            "filterby=active", "search=Pass+A+Vendor+Page", "sort=borrower",
        ):
            self.assertIn(value, page_two[0])

        second_tree = html.fromstring(self.url_open(page_two[0]).content)
        self.assertEqual(len(second_tree.xpath("//table/tbody/tr")), 5)
        self.assertEqual(
            second_tree.xpath("//form[contains(@class,'o_trucalc_order_list_toolbar')]/@action"),
            ["/my/trucalc/orders"],
        )
        self.assertIn(
            "Showing 21–25 of 25", " ".join(second_tree.text_content().split()),
        )
        self.assertNotIn(foreign.order_number, first.text)

        for search_value in (
            fixtures[0].order_number,
            fixtures[0].borrower,
            fixtures[0].property_address,
        ):
            search_result = self.url_open(
                "/my/trucalc/orders?%s" % urlencode({
                    "filterby": "active", "search": search_value,
                })
            ).text
            self.assertIn(fixtures[0].order_number, search_result)
            self.assertNotIn(foreign.order_number, search_result)

        filter_query = {
            **query,
            "status": "open_for_bid", "service": "evaluation",
            "date_field": "invitation",
            "date_from": fields.Date.to_string(fields.Date.today()),
            "date_to": fields.Date.to_string(fields.Date.today()),
        }
        ascending = html.fromstring(self.url_open(
            "/my/trucalc/orders?%s" % urlencode(filter_query)
        ).content)
        borrowers = [
            value.strip() for value in ascending.xpath("//table/tbody/tr/td[3]//text()")
            if value.strip()
        ]
        self.assertEqual(borrowers, sorted(borrowers))

        filter_query["direction"] = "desc"
        descending = html.fromstring(self.url_open(
            "/my/trucalc/orders?%s" % urlencode(filter_query)
        ).content)
        borrowers_desc = [
            value.strip() for value in descending.xpath("//table/tbody/tr/td[3]//text()")
            if value.strip()
        ]
        self.assertEqual(borrowers_desc, sorted(borrowers_desc, reverse=True))

        loan_search = self.url_open(
            "/my/trucalc/orders?filterby=active&search=FORBIDDEN-LOAN-NUMBER"
        )
        self.assertNotIn(fixtures[0].order_number, loan_search.text)
        self.assertNotIn("FORBIDDEN-LOAN-NUMBER", first.text)

        invalid = self.url_open(
            "/my/trucalc/orders?filterby=active&status=company_id"
            "&sort=company_id&direction=drop&date_field=unknown"
        )
        invalid_tree = html.fromstring(invalid.content)
        self.assertIn("list filters were invalid", invalid.text)
        self.assertFalse(invalid_tree.xpath("//table/tbody/tr"))
        self.assertNotIn(foreign.order_number, invalid.text)

    def test_vendor_filter_browser_responsive_and_accessible(self):
        self._authorized_order(self.vendor_a, "Responsive Active")
        code = """
            (() => {
                const header = document.querySelector('.o_trucalc_vendor_header');
                const nav = header.querySelector('.o_trucalc_vendor_filters');
                const controls = [...nav.querySelectorAll('a[data-filter-key]')];
                if (controls.length !== 4) throw Error('Filter count');
                const current = controls.filter(a => a.getAttribute('aria-current') === 'page');
                if (current.length !== 1 || current[0].dataset.filterKey !== 'active') throw Error('Current filter');
                if (getComputedStyle(header).position !== 'sticky') throw Error('Sticky header');
                if (document.documentElement.scrollWidth > document.documentElement.clientWidth + 1) throw Error('Page overflow');
                if (header.getBoundingClientRect().height > innerHeight * .7) throw Error('Header too tall');
                if (controls.some(a => a.getBoundingClientRect().right > innerWidth + 1)) throw Error('Control overflow');
                if (controls.some(a => a.href.includes('/page/'))) throw Error('Filter retains page');
                controls[0].focus();
                if (document.activeElement !== controls[0]) throw Error('Keyboard focus');
                const toggle = document.querySelector('.o_trucalc_filter_toggle');
                const panel = document.querySelector('#trucalc-order-filter-panel');
                const listHeader = document.querySelector('thead.o_trucalc_order_list_header');
                const sortLink = listHeader?.querySelector('.o_trucalc_sort_link');
                const sortIcon = listHeader?.querySelector('.o_trucalc_sort_link .fa');
                const search = document.querySelector('#trucalc-order-search');
                const form = search?.form;
                if (!toggle || !panel || !listHeader || !sortLink || !sortIcon || !form) throw Error('UX controls missing');
                if (panel.classList.contains('show')) throw Error('Filters initially open');
                if (toggle.getAttribute('aria-expanded') !== 'false') throw Error('Initial collapse state');
                if (getComputedStyle(listHeader).backgroundColor !== 'rgb(2, 47, 91)') throw Error('Header background');
                if (getComputedStyle(sortLink).color !== 'rgb(255, 255, 255)') throw Error('Header link color');
                if (getComputedStyle(sortIcon).color !== 'rgb(255, 255, 255)') throw Error('Sort icon color');
                sortLink.focus();
                const focusStyle = getComputedStyle(sortLink);
                if (focusStyle.outlineStyle === 'none' || focusStyle.outlineWidth === '0px') throw Error('Focus indicator');
                const searchWidth = search.getBoundingClientRect().width;
                if (innerWidth >= 992 && Math.abs(searchWidth - 275) > 2) throw Error('Desktop search width');
                if (search.getBoundingClientRect().right > toggle.getBoundingClientRect().left) throw Error('Search alignment');
                let clearSubmitted = false;
                form.submit = () => { clearSubmitted = true; };
                search.value = 'unsubmitted search';
                search.value = '';
                search.dispatchEvent(new Event('input', {bubbles: true}));
                if (!clearSubmitted || search.hasAttribute('name')) throw Error('Search clear submit');
                const clearData = new FormData(form);
                if (clearData.has('search')) throw Error('Search parameter retained');
                if (clearData.get('filterby') !== 'active' || clearData.get('sort') !== 'order_number' || clearData.get('direction') !== 'asc') throw Error('List state not retained');
                if (clearData.get('status') !== 'open_for_bid' || clearData.get('service') !== 'evaluation') throw Error('Other filters not retained');
                if (new URL(form.action).pathname.includes('/page/')) throw Error('Pagination not reset');
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
                    "/my/trucalc/orders?filterby=active&status=open_for_bid&service=evaluation&sort=order_number&direction=asc", code,
                    login=self.vendor_user_a.login,
                    ready=(
                        "document.readyState === 'complete' && "
                        "document.querySelector('.o_trucalc_filter_toggle')?."
                        "dataset.trucalcFilterReady === 'true'"
                    ),
                    timeout=90,
                )

    def test_active_and_submitted_predicates(self):
        invitation, _invite, _projection = self._authorized_order(
            self.vendor_a, "Active Invitation",
        )
        assigned, _ = self._assignment_order("Active Assigned", "assigned")
        engaged, _ = self._assignment_order("Active Engaged", "engaged")
        report, _ = self._assignment_order("Submitted Report", "report_received")
        reviewer, _ = self._assignment_order(
            "Submitted Reviewer", "reviewer_assigned",
        )
        review, _ = self._assignment_order("Submitted Review", "under_review")
        completed, _ = self._assignment_order("Completed Excluded", "completed")

        _response, active_tree = self._listing("active")
        active_text = active_tree.text_content()
        for order in (invitation, assigned, engaged):
            self.assertIn(order.order_number, active_text)
        for order in (report, reviewer, review, completed):
            self.assertNotIn(order.order_number, active_text)

        _response, submitted_tree = self._listing("submitted")
        submitted_text = submitted_tree.text_content()
        for order in (report, reviewer, review):
            self.assertIn(order.order_number, submitted_text)
        for order in (invitation, assigned, engaged, completed):
            self.assertNotIn(order.order_number, submitted_text)

    def test_recent_completion_exact_event_and_inclusive_window(self):
        now = fields.Datetime.to_datetime("2026-09-01 12:00:00")
        recent, _ = self._assignment_order("Recent Complete", "completed")
        boundary, _ = self._assignment_order("Boundary Complete", "completed")
        old, _ = self._assignment_order("Old Complete", "completed")
        future, _ = self._assignment_order("Future Complete", "completed")
        missing, _ = self._assignment_order("Missing Complete", "completed")
        wrong, _ = self._assignment_order("Wrong Event Complete", "completed")
        foreign, _projection = self._engaged_order(
            self.vendor_b, self.vendor_user_b, "Foreign Complete Address",
        )
        self._set_status(foreign, "completed")
        self._event(recent, "order_completed", now - timedelta(days=1))
        self._event(boundary, "order_completed", now - timedelta(days=30))
        self._event(old, "order_completed", now - timedelta(days=30, seconds=1))
        self._event(future, "order_completed", now + timedelta(seconds=1))
        self._event(wrong, "review_accepted", now - timedelta(days=1))
        self._event(foreign, "order_completed", now - timedelta(days=1))

        with patch.object(TruCalcVendorPortal, "_vendor_filter_now", return_value=now):
            _response, tree = self._listing("recent_completed")
        text = tree.text_content()
        for order in (recent, boundary):
            self.assertIn(order.order_number, text)
        for order in (old, future, missing, wrong, foreign):
            self.assertNotIn(order.order_number, text)

    def test_all_history_declined_only_all_and_deauthorization_isolation(self):
        active, _invitation, _projection = self._authorized_order(
            self.vendor_a, "All Active",
        )
        completed, _ = self._assignment_order("All Old Complete", "completed")
        declined, _invitation, declined_projection = self._authorized_order(
            self.vendor_a, "All Declined",
        )
        declined_projection.with_user(self.vendor_user_a).action_vendor_decline(
            "Unavailable",
        )
        revoked, revoked_invitation, _projection = self._authorized_order(
            self.vendor_a, "All Revoked",
        )
        revoked_invitation.with_user(self.admin).action_revoke()
        foreign, _invitation, _projection = self._authorized_order(
            self.vendor_b, "All Foreign",
        )

        _response, all_tree = self._listing("all")
        all_text = all_tree.text_content()
        for order in (active, completed, declined):
            self.assertIn(order.order_number, all_text)
        for order in (revoked, foreign):
            self.assertNotIn(order.order_number, all_text)
        for filterby in ("active", "submitted", "recent_completed"):
            _response, tree = self._listing(filterby)
            self.assertNotIn(declined.order_number, tree.text_content())

    def test_filter_empty_states_and_detail_action_preservation(self):
        messages = {
            "active": "No active TruCalc Orders require current Vendor work.",
            "submitted": (
                "No submitted TruCalc Orders are currently awaiting completion."
            ),
            "recent_completed": (
                "No TruCalc Orders were completed in the last 30 days."
            ),
            "all": "No TruCalc Orders are currently available.",
        }
        self._login(self.vendor_user_b)
        for filterby, message in messages.items():
            response = self.url_open(
                "/my/trucalc/orders?filterby=%s" % filterby
            )
            self.assertIn(message, response.text)

        order, _invitation, _projection = self._authorized_order(
            self.vendor_a, "Actions Preserved",
        )
        self._login(self.vendor_user_a)
        detail = self.url_open("/my/trucalc/orders/%s" % order.order_number)
        for control in (
            "Accept Standard Terms", "Submit Date Change", "Counter Fee",
            "Decline Bid Request",
        ):
            self.assertIn(control, detail.text)
        detail_tree = html.fromstring(detail.content)
        self.assertFalse(detail_tree.xpath(
            '//nav[contains(@class,"o_trucalc_vendor_filters")]'
        ))
