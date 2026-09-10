import ast
from pathlib import Path
from unittest.mock import patch

from lxml import html

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .test_vendor_order_portal import TestVendorOrderPortal


@tagged("post_install", "-at_install", "trucalc_vendor_ux")
class TestVendorUX(TestVendorOrderPortal):
    """Vendor Pass A shell contracts over the existing portal security fixtures."""

    def _vendor_user(self, suffix, vendor=None):
        vendor = vendor or self.env["trucalc.vendor"].create({
            "name": "UX Vendor %s" % suffix,
            "vendor_type": "appraiser",
        })
        user = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "UX Vendor User %s" % suffix,
            "login": "ux-vendor-%s" % suffix,
            "password": self.password,
            "group_ids": [Command.set([
                self.env.ref("trucalc_orders.group_vendor_portal").id,
            ])],
            "trucalc_vendor_id": vendor.id,
        })
        return user, vendor

    def test_vendor_landing_and_account_scope(self):
        self._login(self.vendor_user_a)
        landing = self.url_open("/my?untrusted=1", allow_redirects=False)
        self.assertEqual(landing.status_code, 303)
        self.assertEqual(landing.headers["Location"], "/my/trucalc/orders")
        self.assertEqual(
            self.url_open(landing.headers["Location"], allow_redirects=False).status_code,
            200,
        )
        for path in ("/my/home", "/my/account"):
            response = self.url_open(path, allow_redirects=False)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("o_trucalc_vendor_shell", response.text)

        for actor in (self.plain_portal_user, self.admin):
            self._login(actor)
            response = self.url_open("/my", allow_redirects=False)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("o_trucalc_vendor_shell", response.text)

    def test_invalid_vendor_landing_fails_closed(self):
        self._login(self.vendor_user_a)
        with patch.object(
            type(self.env["res.users"]),
            "_trucalc_vendor_identity",
            side_effect=AccessError("Invalid or mixed identity"),
        ):
            response = self.url_open("/my", allow_redirects=False)
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("Location", response.headers)
        self.assertEqual(self.url_open("/my/home", allow_redirects=False).status_code, 200)

    def test_vendor_identity_helper_fails_closed(self):
        self.assertEqual(
            self.vendor_user_a._trucalc_vendor_identity(), self.vendor_a,
        )

        inactive_user, _vendor = self._vendor_user("inactive-user")
        inactive_user.active = False
        with self.assertRaises(AccessError):
            inactive_user._trucalc_vendor_identity()

        active_user, inactive_vendor = self._vendor_user("inactive-vendor")
        inactive_vendor.active = False
        with self.assertRaises(AccessError):
            active_user._trucalc_vendor_identity()

        unmapped, _vendor = self._vendor_user("unmapped")
        self.env.cr.execute(
            "UPDATE res_users SET trucalc_vendor_id = NULL WHERE id = %s",
            (unmapped.id,),
        )
        unmapped.invalidate_recordset(["trucalc_vendor_id"])
        with self.assertRaises(AccessError):
            unmapped._trucalc_vendor_identity()

        mixed_bank, _vendor = self._vendor_user("mixed-bank")
        self.env.cr.execute(
            "UPDATE res_users SET trucalc_bank_company_id = %s WHERE id = %s",
            (self.env.company.id, mixed_bank.id),
        )
        self.env.cr.execute(
            "INSERT INTO res_groups_users_rel (uid, gid) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING",
            (mixed_bank.id, self.env.ref("trucalc_orders.group_bank_admin").id),
        )
        mixed_bank.invalidate_recordset()
        with self.assertRaises(AccessError):
            mixed_bank._trucalc_vendor_identity()

        mixed_internal, _vendor = self._vendor_user("mixed-internal")
        self.env.cr.execute(
            "INSERT INTO res_groups_users_rel (uid, gid) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING",
            (mixed_internal.id, self.env.ref("trucalc_orders.group_trucalc_operations").id),
        )
        mixed_internal.invalidate_recordset()
        with self.assertRaises(AccessError):
            mixed_internal._trucalc_vendor_identity()

    def test_vendor_shell_footer_breadcrumb_and_workflow_content(self):
        order, _invitation, _projection = self._authorized_order(
            self.vendor_a, "501 Vendor UX Street",
        )
        self._login(self.vendor_user_a)
        issuer = self.env.ref("base.main_company").name
        for path in (
            "/my/trucalc/orders",
            "/my/trucalc/orders/%s" % order.order_number,
        ):
            response = self.url_open(path)
            self.assertEqual(response.status_code, 200)
            tree = html.fromstring(response.content)
            self.assertTrue(tree.xpath(
                '//div[@id="wrapwrap"][contains(@class,"o_trucalc_vendor_shell")]'
            ))
            self.assertTrue(tree.xpath(
                '//header[contains(@class,"o_trucalc_vendor_header")]'
            ))
            self.assertTrue(tree.xpath(
                '//div[contains(@class,"o_trucalc_vendor_portal")]'
            ))
            self.assertFalse(tree.xpath('//ol[contains(@class,"breadcrumb")]'))
            self.assertFalse(tree.xpath('//nav[contains(@class,"o_trucalc_bank_filters")]'))
            self.assertNotIn("o_trucalc_bank_shell", response.text)
            footer = tree.xpath("//footer")[0]
            self.assertIn(issuer, footer.text_content())
            self.assertNotIn(self.vendor_a.name, footer.text_content())
            self.assertFalse(footer.xpath('.//*[contains(@class,"o_brand_promotion")]'))
            self.assertNotIn("Powered by", footer.text_content())

        detail = self.url_open("/my/trucalc/orders/%s" % order.order_number)
        detail_tree = html.fromstring(detail.content)
        self.assertTrue(detail_tree.xpath(
            '//h2[contains(normalize-space(.),"TruCalc Order %s")]' % order.order_number
        ))
        self.assertTrue(detail_tree.xpath(
            '//a[normalize-space()="Back to My TruCalc Orders"]'
        ))
        for control in (
            "Accept Standard Terms", "Submit Date Change", "Counter Fee",
            "Decline Bid Request",
        ):
            self.assertIn(control, detail.text)

    def test_vendor_palette_is_frontend_scoped(self):
        root = Path(__file__).resolve().parents[1]
        manifest = ast.literal_eval((root / "__manifest__.py").read_text())
        frontend = manifest["assets"]["web.assets_frontend"]
        backend = manifest["assets"]["web.assets_backend"]
        self.assertIn("trucalc_orders/static/src/scss/vendor_portal.scss", frontend)
        self.assertNotIn("trucalc_orders/static/src/scss/vendor_portal.scss", backend)
        tokens = (root / "static/src/scss/trucalc_tokens.scss").read_text()
        self.assertIn(".o_trucalc_vendor_shell", tokens)
        self.assertIn(".o_trucalc_vendor_portal", tokens)
        self.assertIn("--trucalc-primary: #022f5b;", tokens)
        self.assertNotIn(":root", tokens)
        self.assertNotIn("$o-brand", tokens)
        vendor_styles = (root / "static/src/scss/vendor_portal.scss").read_text()
        for token in (
            "--trucalc-text", "--trucalc-border", "--trucalc-primary",
            "--trucalc-light-background", "--trucalc-subtle-accent",
        ):
            self.assertIn("var(%s)" % token, vendor_styles)

    def test_bank_landing_remains_unchanged(self):
        bank = self.env["res.company"].with_context(trucalc_test_bank_fixture=True).create({"name": "Vendor UX Bank", "trucalc_is_bank": True, "trucalc_bank_active": True})
        bank_user = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Vendor UX Bank User",
            "login": "vendor-ux-bank-user",
            "password": self.password,
            "group_ids": [Command.set([
                self.env.ref("trucalc_orders.group_bank_admin").id,
            ])],
            "trucalc_bank_company_id": bank.id,
            "company_id": bank.id,
            "company_ids": [Command.set(bank.ids)],
        })
        self._login(bank_user)
        response = self.url_open("/my", allow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["Location"], "/my/trucalc/bank/orders")
        bank_page = self.url_open(response.headers["Location"])
        self.assertIn("o_trucalc_bank_shell", bank_page.text)
        self.assertNotIn("o_trucalc_vendor_shell", bank_page.text)

    def test_vendor_browser_responsive_shell_and_modal(self):
        order, _projection = self._engaged_order(
            self.vendor_a, self.vendor_user_a, "502 Vendor Responsive Street",
        )
        code = """
            (async () => {
                const wait = () => new Promise(resolve => setTimeout(resolve, 700));
                const header = document.querySelector('.o_trucalc_vendor_header');
                const style = getComputedStyle(header);
                if (style.position !== 'sticky' || style.top !== '0px' || Number(style.zIndex) >= 1050) throw Error('Sticky contract');
                if (getComputedStyle(header.querySelector('nav')).backgroundColor !== 'rgb(215, 224, 234)') throw Error('Header palette');
                if (document.querySelector('ol.breadcrumb')) throw Error('Vendor breadcrumb present');
                if (!document.querySelector('h2')?.textContent.includes('TruCalc Order')) throw Error('Detail heading missing');
                if (![...document.querySelectorAll('a')].some(a => a.textContent.trim() === 'Back to My TruCalc Orders')) throw Error('Back link missing');
                document.querySelector('#wrap').style.minHeight = '200vh';
                window.scrollTo(0, 300); await wait();
                if (Math.abs(header.getBoundingClientRect().top) > 1) throw Error('Header did not stick');
                const toggle = header.querySelector('[data-bs-toggle="dropdown"]');
                toggle.click(); await wait();
                const menu = header.querySelector('.dropdown-menu');
                if (!menu.classList.contains('show')) throw Error('Dropdown failed');
                if (menu.getBoundingClientRect().right > innerWidth + 1) throw Error('Dropdown overflow');
                toggle.click();
                window.scrollTo(0, 0); await wait();
                document.querySelector('#accept-engagement-modal').previousElementSibling.querySelector('[data-bs-target="#accept-engagement-modal"]').click();
                await wait();
                const modal = document.querySelector('.modal.show');
                if (!modal || Number(getComputedStyle(modal).zIndex) <= Number(style.zIndex)) throw Error('Modal stacking');
                if (!modal.contains(document.activeElement)) throw Error('Modal focus');
                modal.querySelector('[data-bs-dismiss="modal"]').click(); await wait();
                if (document.querySelector('.modal.show, .modal-backdrop')) throw Error('Modal did not close');
                document.documentElement.style.zoom = '2'; await wait();
                if (document.documentElement.scrollWidth > document.documentElement.clientWidth + 1) throw Error('Zoom page overflow');
                if (header.getBoundingClientRect().height > innerHeight * .7) throw Error('Sticky header too tall');
                console.log('test successful');
            })();
        """
        for size in ("1366x768", "768x1024", "390x844"):
            with self.subTest(size=size):
                self.browser_size = size
                self.browser_js(
                    "/my/trucalc/orders/%s" % order.order_number,
                    code,
                    login=self.vendor_user_a.login,
                    ready='!!document.querySelector("#accept-engagement-modal")',
                    timeout=90,
                )
