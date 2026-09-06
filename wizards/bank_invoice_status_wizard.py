from odoo import fields, models
from odoo.exceptions import AccessError


class BankInvoiceStatusWizard(models.TransientModel):
    _name = "trucalc.bank.invoice.status.wizard"
    _description = "Bank Invoice Status Action"

    invoice_id = fields.Many2one("trucalc.bank.invoice", required=True, readonly=True)
    transition = fields.Selection([("paid", "Paid"), ("void", "Void")], required=True, readonly=True)
    payment_date = fields.Date(string="Date Paid", default=fields.Date.today)
    payment_reference = fields.Char(string="Check # / Payment Reference", size=255)
    reason = fields.Text(string="Void Reason")

    def action_confirm(self):
        self.ensure_one()
        if self.transition == "paid":
            self.invoice_id._mark_paid(self.payment_date, self.payment_reference)
        elif self.transition == "void":
            self.invoice_id._void(self.reason)
        else:
            raise AccessError("Invalid invoice transition.")
        return {"type": "ir.actions.act_window_close"}
