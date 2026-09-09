from datetime import timedelta
from pathlib import Path
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
        with patch.object(TruCalcVendorPortal, "_items_per_page", 2):
            _response, tree = self._listing("active")
        page_two = tree.xpath('//a[contains(@href,"/page/2")]/@href')
        self.assertTrue(page_two)
        self.assertTrue(any("filterby=active" in href for href in page_two))

        styles = (Path(__file__).resolve().parents[1] /
                  "static/src/scss/vendor_portal.scss").read_text()
        self.assertIn(".o_trucalc_vendor_filters", styles)
        self.assertIn("flex-wrap: wrap", styles)
        self.assertNotIn("height:", styles)

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
                console.log('test successful');
            })();
        """
        for size in ("1366x768", "768x1024", "390x844"):
            with self.subTest(size=size):
                self.browser_size = size
                self.browser_js(
                    "/my/trucalc/orders", code,
                    login=self.vendor_user_a.login,
                    ready="!!document.querySelector('.o_trucalc_vendor_filters')",
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
