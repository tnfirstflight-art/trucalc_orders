from odoo import fields, models, _
from odoo.exceptions import ValidationError


class TruCalcReviewerAssignmentWizard(models.TransientModel):
    _name = "trucalc.reviewer.assignment.wizard"
    _description = "Assign TruCalc Reviewer"

    order_id = fields.Many2one(
        "trucalc.order", required=True, readonly=True, ondelete="cascade",
    )
    reviewer_user_id = fields.Many2one(
        "res.users", string="Reviewer", required=True,
        domain="[('active', '=', True), ('share', '=', False)]",
    )
    reason = fields.Text(string="Reason for Reassignment")

    def action_confirm(self):
        self.ensure_one()
        if self.order_id.status == "report_received":
            self.order_id.action_assign_reviewer(self.reviewer_user_id)
        else:
            if not self.reason or not self.reason.strip():
                raise ValidationError(_("A reassignment reason is required."))
            self.order_id.action_reassign_reviewer(
                self.reviewer_user_id, self.reason,
            )
        return {"type": "ir.actions.act_window_close"}
