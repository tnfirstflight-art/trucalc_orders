import ast
from pathlib import Path
from unittest.mock import patch

from lxml import html

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import tagged

from .test_bank_order_portal import TestBankOrderPortal


@tagged('post_install', '-at_install', 'trucalc_bank_ux')
class TestBankUX(TestBankOrderPortal):
    """Pass A presentation contracts, with the existing Bank security fixtures."""

    def test_login_scope_and_footer(self):
        self.authenticate(None, None)
        response = self.url_open('/web/login?redirect=%2Fodoo%3F')
        self.assertEqual(response.status_code, 200)
        tree = html.fromstring(response.content)
        card = tree.xpath('//div[contains(@class,"o_trucalc_login")]')
        self.assertEqual(len(card), 1)
        self.assertFalse(card[0].xpath('.//div[contains(@class,"border-top")]'))
        self.assertTrue(card[0].xpath('.//div[contains(@class,"border-bottom")]'))
        self.assertNotIn('Manage Databases', response.text)
        self.assertNotIn('Powered by', response.text)
        self.assertTrue(card[0].xpath('.//input[@name="redirect"][@value="/odoo?"]'))
        self.assertEqual(self.url_open('/web/database/manager').status_code, 200)
        # Other callers of login_layout do not acquire the login-only marker.
        reset = self.url_open('/web/reset_password').text
        self.assertNotIn('o_trucalc_login', reset)

    def test_bank_landing_and_account_scope(self):
        for actor in (self.bank_admin, self.bank_requestor, self.bank_viewer):
            self._login(actor)
            response = self.url_open('/my?untrusted=1', allow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers['Location'], '/my/trucalc/bank/orders')
            self.assertEqual(self.url_open(response.headers['Location'], allow_redirects=False).status_code, 200)
            account = self.url_open('/my/home', allow_redirects=False)
            self.assertEqual(account.status_code, 200)
            self.assertNotIn('o_trucalc_bank_shell', account.text)
            self.assertNotIn('o_trucalc_bank_header', account.text)
            self.assertIn('o_brand_promotion', account.text)
            self.assertEqual(self.url_open('/my/account', allow_redirects=False).status_code, 200)
        for actor in (self.plain_portal, self.admin, self.legacy_internal_bank_user):
            self._login(actor)
            response = self.url_open('/my', allow_redirects=False)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('o_trucalc_bank_shell', response.text)

    def test_invalid_bank_landing_fails_closed(self):
        self._login(self.bank_admin)
        # Covers the existing validator rejecting invalid/mixed Bank identities.
        with patch.object(type(self.env['res.users']), '_trucalc_bank_identity',
                          side_effect=AccessError('Invalid or mixed identity')):
            response = self.url_open('/my', allow_redirects=False)
            self.assertEqual(response.status_code, 404)
            self.assertNotIn('Location', response.headers)
            self.assertEqual(self.url_open('/my/home', allow_redirects=False).status_code, 200)

    def test_bank_shell_identity_and_pass_b_unchanged(self):
        self._login(self.bank_admin)
        issuer = self.env.ref('base.main_company').name
        for path in ('/my/trucalc/bank/orders', '/my/trucalc/bank/orders/new',
                     '/my/trucalc/bank/orders/%s/documents' % self.order_a.order_number):
            response = self.url_open(path)
            self.assertEqual(response.status_code, 200)
            tree = html.fromstring(response.content)
            self.assertTrue(tree.xpath('//div[@id="wrapwrap"][contains(@class,"o_trucalc_bank_shell")]'))
            self.assertTrue(tree.xpath('//header[contains(@class,"o_trucalc_bank_header")]'))
            self.assertTrue(tree.xpath('//div[contains(@class,"o_trucalc_bank_portal")]'))
            self.assertTrue(tree.xpath('//header//a[contains(@class,"logo")]/img'))
            self.assertTrue(tree.xpath('//header//a[@href="/my/home"]'))
            self.assertTrue(tree.xpath('//header//a[@id="o_logout"]'))
            footer = tree.xpath('//footer')[0]
            self.assertIn(issuer, footer.text_content())
            self.assertNotIn(self.bank_a.name, footer.text_content())
            self.assertFalse(footer.xpath('.//*[contains(@class,"o_brand_promotion")]'))
            self.assertNotIn('Powered by', footer.text_content())
            self.assertTrue(tree.xpath('//ol[contains(@class,"breadcrumb")]'))
            self.assertFalse(tree.xpath('//a[contains(@href,"filterby=")]'))
            if path.endswith('/documents'):
                self.assertTrue(tree.xpath('//h2[contains(.,"TruCalc Request")]'))
                self.assertTrue(tree.xpath('//a[normalize-space()="Back to My TruCalc Requests"]'))

    def test_vendor_shell_and_home_isolation(self):
        vendor = self.env['trucalc.vendor'].create({'name': 'UX Vendor', 'vendor_type': 'appraiser'})
        user = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'UX Vendor', 'login': 'ux-vendor', 'password': self.password,
            'group_ids': [Command.set([self.env.ref('trucalc_orders.group_vendor_portal').id])],
            'trucalc_vendor_id': vendor.id,
        })
        self._login(user)
        for path in ('/my', '/my/home', '/my/trucalc/orders'):
            response = self.url_open(path, allow_redirects=False)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('o_trucalc_bank_shell', response.text)
            self.assertNotIn('o_trucalc_bank_header', response.text)
            self.assertNotIn('o_trucalc_bank_portal', response.text)
            self.assertIn('o_brand_promotion', response.text)

    def test_asset_scope_contract(self):
        root = Path(__file__).resolve().parents[1]
        manifest = ast.literal_eval((root / '__manifest__.py').read_text())
        self.assertEqual(manifest['version'], '1.23')
        self.assertNotIn('web._assets_primary_variables', manifest['assets'])
        self.assertFalse(any('trucalc_tokens' in p or 'bank_portal.scss' in p or 'trucalc_login' in p
                             for p in manifest['assets']['web.assets_backend']))
        tokens = (root / 'static/src/scss/trucalc_tokens.scss').read_text()
        self.assertIn('--trucalc-primary: #022f5b;', tokens)
        self.assertNotIn(':root', tokens)
        self.assertNotIn('$o-brand', tokens)

    def test_login_computed_branding(self):
        self.browser_js('/web/login', """
            (async () => {
            const card = document.querySelector('.o_trucalc_login');
            const style = getComputedStyle(card);
            if (style.getPropertyValue('--trucalc-primary').trim() !== '#022f5b') throw Error('Missing token');
            for (const link of card.querySelectorAll('.btn-link, .passkey_login_link')) {
                if (getComputedStyle(link).color !== 'rgb(2, 47, 91)') throw Error('Unbranded link');
            }
            const button = card.querySelector('.btn-primary');
            if (getComputedStyle(button).backgroundColor !== 'rgb(2, 47, 91)') throw Error('Unbranded button');
            const input = card.querySelector('input[name="login"]');
            input.focus();
            await new Promise(resolve => setTimeout(resolve, 300));
            if (getComputedStyle(input).borderColor !== 'rgb(2, 47, 91)') throw Error('Unbranded input focus');
            if (getComputedStyle(document.body).getPropertyValue('--trucalc-primary')) throw Error('Global token leak');
            console.log('test successful');
            })();
        """, ready="!!document.querySelector('.o_trucalc_login .passkey_login_link')")

    def test_bank_browser_responsive_shell(self):
        order = self.env['trucalc.order'].with_user(self.bank_requestor)._create_bank_draft(
            self._complete_draft_values(), self.bank_requestor)
        order.with_user(self.bank_requestor)._send_bank_draft(order, self._complete_draft_values(), self.bank_requestor)
        order.with_user(self.admin).action_accept_request()
        order.with_user(self.admin).action_request_fee_change(650, 'Responsive modal check')
        code = """
            (async () => {
                const wait = () => new Promise(resolve => setTimeout(resolve, 700));
                const header = document.querySelector('.o_trucalc_bank_header');
                const s = getComputedStyle(header);
                if (s.position !== 'sticky' || s.top !== '0px' || Number(s.zIndex) >= 1050) throw Error('Sticky contract');
                if (getComputedStyle(header.querySelector('nav')).backgroundColor !== 'rgb(215, 224, 234)') throw Error('Header palette');
                // Ensure scrolling even on a small fixture page, without changing its layout rules.
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
                document.querySelector('[data-bs-toggle="modal"]').click(); await wait();
                const modal = document.querySelector('.modal.show');
                if (!modal || Number(getComputedStyle(modal).zIndex) <= Number(s.zIndex)) throw Error('Modal stacking');
                if (!modal.contains(document.activeElement)) throw Error('Modal focus');
                modal.querySelector('[data-bs-dismiss="modal"]').click(); await wait();
                if (document.querySelector('.modal.show, .modal-backdrop')) throw Error('Modal did not close');
                document.documentElement.style.zoom = '2'; await wait();
                if (document.documentElement.scrollWidth > document.documentElement.clientWidth + 1) throw Error('Zoom page overflow');
                console.log('test successful');
            })();
        """
        for size in ('1366x768', '768x1024', '390x844'):
            with self.subTest(size=size):
                self.browser_size = size
                self.browser_js('/my/trucalc/bank/orders', code, login=self.bank_admin.login,
                                ready="!!document.querySelector('[data-bs-toggle=\"modal\"]')", timeout=90)

    def test_detail_create_responsive_header(self):
        code = """
            (async () => {
                const wait = () => new Promise(resolve => setTimeout(resolve, 700));
                const header = document.querySelector('.o_trucalc_bank_header');
                const logo = header.querySelector('.logo');
                const toggle = header.querySelector('[data-bs-toggle="dropdown"]');
                for (const zoom of ['1', '2']) {
                    document.documentElement.style.zoom = zoom;
                    document.querySelector('#wrap').style.minHeight = '200vh';
                    window.scrollTo(0, 250); await wait();
                    if (Math.abs(header.getBoundingClientRect().top) > 1) throw Error('Header not sticky');
                    const a = logo.getBoundingClientRect(), b = toggle.getBoundingClientRect();
                    if (a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top) throw Error('Logo overlaps dropdown');
                    toggle.click(); await wait();
                    if (!header.querySelector('.dropdown-menu.show')) throw Error('Dropdown unavailable');
                    toggle.click();
                }
                console.log('test successful');
            })();
        """
        for size in ('1366x768', '768x1024', '390x844'):
            for path in ('/my/trucalc/bank/orders/new',
                         '/my/trucalc/bank/orders/%s/documents' % self.order_a.order_number):
                with self.subTest(size=size, path=path):
                    self.browser_size = size
                    self.browser_js(path, code, login=self.bank_admin.login,
                                    ready="document.readyState === 'complete'", timeout=90)
