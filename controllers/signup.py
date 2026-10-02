import binascii

from odoo import http
from odoo.http import request

from odoo.addons.auth_signup.controllers.main import AuthSignupHome


class TruCalcAuthSignupHome(AuthSignupHome):
    """Keep invitation handling canonical without changing token semantics."""

    @staticmethod
    def _trucalc_invalid_invitation_response():
        return request.redirect("/web/login")

    def _trucalc_signup_partner(self, token, lock=False):
        Partner = request.env["res.partner"].sudo()
        if not isinstance(token, str) or not token:
            return Partner.browse()
        try:
            partner = Partner._get_partner_from_token(token)
        except (binascii.Error, TypeError, UnicodeError, ValueError):
            return Partner.browse()
        if not partner:
            return partner
        if lock:
            request.env.cr.execute(
                "SELECT id FROM res_partner WHERE id = %s FOR UPDATE",
                [partner.id],
            )
            request.env.invalidate_all()
            try:
                partner = Partner._get_partner_from_token(token)
            except (binascii.Error, TypeError, UnicodeError, ValueError):
                return Partner.browse()
        return partner.exists()

    @staticmethod
    def _trucalc_is_internal_invitation(partner):
        user = partner.user_ids[:1]
        if not user:
            return False
        membership = user._trucalc_persona_membership()
        return bool(
            len(membership["internal"]) == 1
            and not membership["bank"]
            and not membership["vendor"]
            and not user.trucalc_bank_company_id
            and not user.trucalc_vendor_id
        )

    @http.route()
    def web_auth_signup(self, *args, **kw):
        token = request.params.get("token")
        if not token:
            if self.get_auth_signup_config().get("signup_enabled"):
                return super().web_auth_signup(*args, **kw)
            return self._trucalc_invalid_invitation_response()
        partner = self._trucalc_signup_partner(
            token,
            lock=request.httprequest.method == "POST",
        )
        if not partner:
            return self._trucalc_invalid_invitation_response()
        request.update_context(trucalc_signup_partner_id=partner.id)
        return super().web_auth_signup(*args, **kw)

    def get_auth_signup_qcontext(self):
        qcontext = super().get_auth_signup_qcontext()
        partner_id = request.env.context.get("trucalc_signup_partner_id")
        partner = request.env["res.partner"].sudo().browse(partner_id).exists()
        qcontext["trucalc_internal_signup"] = bool(
            partner and self._trucalc_is_internal_invitation(partner)
        )
        return qcontext
