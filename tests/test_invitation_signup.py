from contextlib import contextmanager
import json
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from lxml import html

from odoo import Command, http
from odoo.addons.mail.models.mail_template import MailTemplate
from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install", "trucalc_invitation_signup")
class TestInvitationSignup(HttpCase):
    password = "Pass-B-Synthetic-Password"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Companies = cls.env["res.company"].with_context(
            trucalc_test_bank_fixture=True,
        )
        cls.bank = Companies.create({
            "name": "Pass B Invitation Bank",
            "currency_id": cls.env.company.currency_id.id,
            "trucalc_is_bank": True,
            "trucalc_bank_active": True,
        })
        cls.vendor = cls.env["trucalc.vendor"].create({
            "name": "Pass B Invitation Vendor",
            "vendor_type": "appraiser",
        })
        cls.bank_user = cls._user(
            "bank",
            [cls.env.ref("trucalc_orders.group_bank_requestor")],
            bank=cls.bank,
        )
        cls.vendor_user = cls._user(
            "vendor",
            [cls.env.ref("trucalc_orders.group_vendor_portal")],
            vendor=cls.vendor,
        )
        cls.internal_user = cls._user(
            "internal",
            [cls.env.ref("trucalc_orders.group_trucalc_operations")],
            internal=True,
        )
        cls.expired_user = cls._user(
            "expired",
            [cls.env.ref("trucalc_orders.group_bank_view_only")],
            bank=cls.bank,
        )
        cls.consumed_user = cls._user(
            "consumed",
            [cls.env.ref("trucalc_orders.group_bank_view_only")],
            bank=cls.bank,
        )
        cls.validation_user = cls._user(
            "validation",
            [cls.env.ref("trucalc_orders.group_bank_view_only")],
            bank=cls.bank,
        )
        cls.browser_user = cls._user(
            "browser",
            [cls.env.ref("trucalc_orders.group_bank_requestor")],
            bank=cls.bank,
        )

    @classmethod
    def _user(cls, suffix, groups, bank=False, vendor=False, internal=False):
        values = {
            "name": "Pass B %s User" % suffix.title(),
            "login": "pass-b-%s@example.test" % suffix,
            "email": "pass-b-%s@example.test" % suffix,
            "group_ids": [Command.set([group.id for group in groups])],
            "trucalc_bank_company_id": bank.id if bank else False,
            "trucalc_vendor_id": vendor.id if vendor else False,
        }
        if bank:
            values.update({
                "company_id": bank.id,
                "company_ids": [Command.set([bank.id])],
            })
        elif internal:
            values.update({
                "company_id": cls.env.ref("base.main_company").id,
                "company_ids": [Command.set(
                    cls.env["res.users"]._trucalc_internal_companies().ids
                )],
            })
        return cls.env["res.users"].with_context(
            no_reset_password=True,
        ).create(values)

    @contextmanager
    def _signup_http_patches(self):
        def verify_captcha(_model, captcha):
            self.assertEqual(captcha, "signup")

        with patch.object(
            self.env.registry["ir.http"],
            "_verify_request_recaptcha_token",
            verify_captcha,
        ), patch.object(
            MailTemplate,
            "send_mail",
            autospec=True,
            return_value=1,
        ):
            yield

    def _invitation(self, user):
        user.partner_id.signup_prepare(signup_type="signup")
        url = user.partner_id._get_signup_url()
        split = urlsplit(url)
        token = parse_qs(split.query)["token"][0]
        return "%s?%s" % (split.path, split.query), token

    def _signup(self, user, expected_final_path):
        invitation_path, token = self._invitation(user)
        self.authenticate(None, None)
        get_response = self.url_open(invitation_path, allow_redirects=False)
        self.assertEqual(get_response.status_code, 200)
        tree = html.fromstring(get_response.text)
        forms = tree.xpath("//form[contains(concat(' ', normalize-space(@class), ' '), ' oe_signup_form ')]")
        self.assertEqual(len(forms), 1)
        form = forms[0]
        self.assertEqual(form.get("action"), "/web/signup")
        self.assertNotIn("token", form.get("action"))
        hidden = {
            field.get("name"): field.get("value", "")
            for field in form.xpath(".//input[@type='hidden'][@name]")
        }
        self.assertTrue(hidden["csrf_token"])
        self.assertEqual(hidden["token"], token)
        self.assertEqual(hidden["db"], self.env.cr.dbname)
        payload = {
            **hidden,
            "name": user.name,
            "login": user.login,
            "password": self.password,
            "confirm_password": self.password,
        }
        with self._signup_http_patches():
            response = self.url_open(
                "/web/signup",
                data=payload,
                allow_redirects=True,
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(urlsplit(str(response.url)).path.startswith(expected_final_path))
        locations = [item.headers.get("Location", "") for item in response.history]
        self.assertTrue(locations)
        self.assertTrue(all("token=" not in location for location in locations))
        if expected_final_path.startswith("/my/"):
            self.assertTrue(any(urlsplit(location).path == "/my" for location in locations))
        else:
            self.assertTrue(any(urlsplit(location).path.startswith("/odoo") for location in locations))

        self.env.invalidate_all()
        partner = self.env["res.partner"].browse(user.partner_id.id)
        self.assertFalse(partner.signup_type)
        self.assertFalse(partner._get_partner_from_token(token))
        revisit = self.url_open(invitation_path, allow_redirects=False)
        self._assert_neutral(revisit)
        return token

    def _assert_neutral(self, response):
        self.assertEqual(response.status_code, 303)
        self.assertEqual(urlsplit(response.headers["Location"]).path, "/web/login")
        self.assertNotIn("token", response.headers["Location"])

    def test_bank_signup_canonical_post_and_safe_consumed_revisit(self):
        self._signup(self.bank_user, "/my/trucalc/bank/orders")

    def test_bank_signup_browser_canonical_flow(self):
        self.env["ir.config_parameter"].set_param(
            "auth_signup.invitation_scope", "b2b",
        )
        invitation_path, token = self._invitation(self.browser_user)
        password = json.dumps(self.password)
        code = """
            (async () => {
                const form = document.querySelector('form.oe_signup_form');
                if (!form) throw new Error('Missing signup form');
                const action = new URL(form.action, location.origin);
                if (action.pathname !== '/web/signup' || action.search) {
                    throw new Error('Signup action is not canonical');
                }
                const hiddenToken = form.querySelector('input[name="token"]');
                if (!hiddenToken || hiddenToken.value !== %s) {
                    throw new Error('Signed token is not preserved in hidden data');
                }
                const data = new FormData(form);
                data.set('password', %s);
                data.set('confirm_password', %s);
                const response = await fetch(action.pathname, {
                    method: 'POST', body: data, redirect: 'follow',
                });
                const finalUrl = new URL(response.url);
                if (response.status !== 200 ||
                    finalUrl.pathname !== '/my/trucalc/bank/orders' ||
                    finalUrl.searchParams.has('token')) {
                    throw new Error('Stable Bank destination was not preserved');
                }
                const revisit = await fetch('/web/signup', {redirect: 'follow'});
                const revisitUrl = new URL(revisit.url);
                if (revisit.status >= 400 ||
                    revisitUrl.pathname === '/web/signup' ||
                    revisitUrl.searchParams.has('token')) {
                    throw new Error('Tokenless revisit did not fail safely');
                }
                const malformed = await fetch(
                    '/web/signup?token=not-a-valid-signed-token!',
                    {redirect: 'follow'},
                );
                const malformedUrl = new URL(malformed.url);
                if (malformed.status >= 400 ||
                    malformedUrl.pathname === '/web/signup' ||
                    malformedUrl.searchParams.has('token')) {
                    throw new Error('Malformed-token revisit did not fail safely');
                }
                console.log('test successful');
            })();
        """ % (json.dumps(token), password, password)
        self.authenticate(None, None)
        with self._signup_http_patches():
            self.browser_js(
                invitation_path,
                code,
                ready="!!document.querySelector('form.oe_signup_form')",
            )
        self.env.invalidate_all()
        partner = self.env["res.partner"].browse(self.browser_user.partner_id.id)
        self.assertFalse(partner.signup_type)
        self.assertFalse(partner._get_partner_from_token(token))

    def test_vendor_signup_canonical_post_and_safe_consumed_revisit(self):
        self._signup(self.vendor_user, "/my/trucalc/orders")

    def test_internal_signup_canonical_post_and_safe_consumed_revisit(self):
        invitation_path, _token = self._invitation(self.internal_user)
        self.authenticate(None, None)
        response = self.url_open(invitation_path, allow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertIn("o_trucalc_login", response.text)
        self.assertNotIn("Powered by", response.text)
        self._signup(self.internal_user, "/odoo")

    def test_valid_token_validation_error_preserves_canonical_form(self):
        invitation_path, token = self._invitation(self.validation_user)
        self.authenticate(None, None)
        initial = self.url_open(invitation_path)
        initial_tree = html.fromstring(initial.text)
        csrf_token = initial_tree.xpath(
            "string(//form[contains(@class, 'oe_signup_form')]"
            "/input[@name='csrf_token']/@value)"
        )
        with self._signup_http_patches():
            response = self.url_open("/web/signup", data={
                "csrf_token": csrf_token,
                "db": self.env.cr.dbname,
                "token": token,
                "name": self.validation_user.name,
                "login": self.validation_user.login,
                "password": self.password,
                "confirm_password": self.password + "-mismatch",
            })
        self.assertEqual(response.status_code, 200)
        tree = html.fromstring(response.text)
        forms = tree.xpath("//form[contains(@class, 'oe_signup_form')]")
        self.assertEqual(len(forms), 1)
        self.assertEqual(forms[0].get("action"), "/web/signup")
        self.assertEqual(
            forms[0].xpath("string(.//input[@name='token']/@value)"),
            token,
        )
        self.assertIn("Passwords do not match", response.text)
        self.env.invalidate_all()
        partner = self.env["res.partner"].browse(
            self.validation_user.partner_id.id
        )
        self.assertEqual(partner.signup_type, "signup")
        self.assertEqual(partner._get_partner_from_token(token), partner)

    def test_invalid_invitation_get_and_post_are_neutral(self):
        self.env["ir.config_parameter"].set_param(
            "auth_signup.invitation_scope", "b2b",
        )
        self.expired_user.partner_id.signup_prepare(signup_type="signup")
        expired = self.expired_user.partner_id._generate_signup_token(
            expiration=-1,
        )
        self.consumed_user.partner_id.signup_prepare(signup_type="signup")
        consumed = self.consumed_user.partner_id._generate_signup_token()
        self.consumed_user.partner_id.signup_cancel()
        malformed = "not-a-valid-signed-token!"

        self.authenticate(None, None)
        get_responses = [
            self.url_open("/web/signup", allow_redirects=False),
            self.url_open(
                "/web/signup", params={"token": malformed},
                allow_redirects=False,
            ),
            self.url_open(
                "/web/signup", params={"token": expired},
                allow_redirects=False,
            ),
            self.url_open(
                "/web/signup", params={"token": consumed},
                allow_redirects=False,
            ),
        ]
        for response in get_responses:
            self._assert_neutral(response)

        csrf_token = http.Request.csrf_token(self)
        base_payload = {
            "csrf_token": csrf_token,
            "name": "Rejected Invitation",
            "login": self.expired_user.login,
            "password": self.password,
            "confirm_password": self.password,
        }
        post_tokens = (None, malformed, expired, consumed)
        with self._signup_http_patches():
            post_responses = []
            for token in post_tokens:
                payload = dict(base_payload)
                if token:
                    payload["token"] = token
                post_responses.append(self.url_open(
                    "/web/signup", data=payload, allow_redirects=False,
                ))
        for response in post_responses:
            self._assert_neutral(response)
        responses = get_responses + post_responses
        self.assertEqual(
            {(response.status_code, response.headers["Location"])
             for response in responses},
            {(303, "/web/login")},
        )
        self.assertEqual(len({response.text for response in responses}), 1)
        neutral_body = responses[0].text
        for private_value in (
            self.expired_user.login,
            self.expired_user.name,
            malformed,
            expired,
            consumed,
        ):
            self.assertNotIn(private_value, neutral_body)

        self.env.invalidate_all()
        self.assertEqual(self.expired_user.partner_id.signup_type, "signup")
        self.assertFalse(self.consumed_user.partner_id.signup_type)
