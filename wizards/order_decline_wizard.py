from odoo import fields, models, _
from odoo.exceptions import ValidationError


class TruCalcOrderDeclineWizard(models.TransientModel):
    _name = "trucalc.order.decline.wizard"
    _description = "Decline TruCalc Request"

    order_id = fields.Many2one(
        "trucalc.order",
        string="Request",
        required=True,
        readonly=True,
    )
    reason = fields.Text(string="Reason for Decline", required=True)

    def action_decline(self):
        self.ensure_one()
        self.env["trucalc.order"]._require_intake_manager()
        if not self.reason or not self.reason.strip():
            raise ValidationError(_("A meaningful reason for decline is required."))
        self.order_id.action_decline_request(self.reason)
        return {"type": "ir.actions.act_window_close"}
