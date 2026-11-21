from odoo import fields, models, _
from odoo.exceptions import ValidationError

from ..models.decline_policy import ORDER_DECLINE_CATEGORY_SELECTION


class TruCalcOrderPostAcceptanceDeclineWizard(models.TransientModel):
    _name = "trucalc.order.post.acceptance.decline.wizard"
    _description = "Decline TruCalc Order After Acceptance"

    order_id = fields.Many2one(
        "trucalc.order", string="Order", required=True, readonly=True,
    )
    category = fields.Selection(
        ORDER_DECLINE_CATEGORY_SELECTION,
        required=True,
        default="inspection_findings",
    )
    reason = fields.Text(
        string="Decline Reason", required=True,
        help="Record the inspection findings or other business reason.",
    )
    vendor_notification_note = fields.Text(
        string="Manual Vendor Communication",
        required=True,
        help=(
            "Record how and when the Vendor was manually told to stop work. "
            "This release does not send an automatic Vendor notification."
        ),
    )

    def action_decline(self):
        self.ensure_one()
        self.env["trucalc.order"]._require_intake_manager()
        reason = self.reason.strip() if self.reason else ""
        notification = (
            self.vendor_notification_note.strip()
            if self.vendor_notification_note else ""
        )
        if not reason:
            raise ValidationError(_("A meaningful decline reason is required."))
        if not notification:
            raise ValidationError(_(
                "Record the manual Vendor communication before declining the Order."
            ))
        self.order_id.action_decline_after_acceptance(
            self.category, reason, notification,
        )
        return {"type": "ir.actions.act_window_close"}
