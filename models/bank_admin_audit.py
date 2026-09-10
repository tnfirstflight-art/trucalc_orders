from odoo import api, fields, models, _
from odoo.exceptions import AccessError


class TruCalcBankAdminAudit(models.Model):
    _name = "trucalc.bank.admin.audit"
    _description = "TruCalc Bank Administration Audit"
    _order = "event_at desc, id desc"

    event_type = fields.Selection([
        ("bank_created", "Bank Created"),
        ("bank_activated", "Bank Activated"),
        ("bank_deactivated", "Bank Deactivated"),
        (
            "bank_user_deactivated_by_bank_deactivation",
            "Bank User Deactivated by Bank Deactivation",
        ),
    ], required=True, readonly=True, index=True)
    event_at = fields.Datetime(required=True, readonly=True, index=True)
    actor_user_id = fields.Many2one(
        "res.users", required=True, readonly=True, ondelete="restrict", index=True,
    )
    bank_company_id = fields.Many2one(
        "res.company", required=True, readonly=True, ondelete="restrict", index=True,
    )
    target_user_id = fields.Many2one(
        "res.users", readonly=True, ondelete="restrict", index=True,
    )
    normalized_identity = fields.Char(readonly=True)
    prior_status = fields.Char(readonly=True)
    new_status = fields.Char(readonly=True)
    metadata = fields.Json(readonly=True)

    @api.model
    @api.private
    def _trucalc_log(
        self, event_type, actor, bank, target_user=False,
        prior_status=False, new_status=False, metadata=None,
    ):
        actor.ensure_one()
        bank.ensure_one()
        if actor != self.env.user:
            raise AccessError(_("The Bank administration audit actor is invalid."))
        self.env["res.company"]._trucalc_require_bank_administrator()
        bank._trucalc_bank_identity_record(require_active=False)
        if target_user:
            target_user.ensure_one()
        return super(TruCalcBankAdminAudit, self.sudo()).create({
            "event_type": event_type,
            "event_at": fields.Datetime.now(),
            "actor_user_id": actor.id,
            "bank_company_id": bank.id,
            "target_user_id": target_user.id if target_user else False,
            "normalized_identity": (
                (target_user.login or "").strip().casefold() if target_user else False
            ),
            "prior_status": prior_status,
            "new_status": new_status,
            "metadata": metadata or {},
        })

    @api.model_create_multi
    def create(self, vals_list):
        raise AccessError(_("Bank administration audit events are system controlled."))

    def write(self, vals):
        raise AccessError(_("Bank administration audit events are immutable."))

    def unlink(self):
        raise AccessError(_("Bank administration audit events cannot be deleted."))

    def copy(self, default=None):
        raise AccessError(_("Bank administration audit events cannot be copied."))
