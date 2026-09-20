from odoo import models
from odoo.http import request


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    def session_info(self):
        result = super().session_info()
        result["trucalc_hide_company_selector"] = bool(
            request.session.uid
            and self.env.user._trucalc_should_hide_company_selector()
        )
        return result
