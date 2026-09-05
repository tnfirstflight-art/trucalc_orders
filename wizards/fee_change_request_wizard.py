from odoo import fields, models


class FeeChangeRequestWizard(models.TransientModel):
    _name = "trucalc.fee.change.request.wizard"
    _description = "Request Fee Change"

    order_id = fields.Many2one("trucalc.order", required=True, readonly=True)
    currency_id = fields.Many2one(related="order_id.fee_currency_id", readonly=True)
    current_fee = fields.Monetary(related="order_id.current_agreed_fee", readonly=True)
    proposed_fee = fields.Monetary(required=True)
    reason = fields.Text(required=True)

    def action_request(self):
        self.ensure_one()
        self.order_id.action_request_fee_change(self.proposed_fee, self.reason)
        return {"type": "ir.actions.act_window_close"}
