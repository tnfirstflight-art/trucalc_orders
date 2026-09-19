from odoo import api, fields, models, _
from odoo.exceptions import AccessError


class TruCalcInternalUserAdminAudit(models.Model):
    _name = "trucalc.internal.user.admin.audit"
    _description = "TruCalc Internal User Administration Audit"
    _order = "event_at desc, id desc"

    event_type = fields.Selection([
        ("user_provisioned", "Internal User Provisioned"),
        ("existing_partner_reused", "Existing Partner Reused"),
        ("role_changed", "Internal User Role Changed"),
        ("reviewer_added", "Reviewer Capability Added"),
        ("reviewer_removed", "Reviewer Capability Removed"),
        ("user_deactivated", "Internal User Deactivated"),
        ("user_reactivated", "Internal User Reactivated"),
        ("invitation_sent", "Internal User Invitation Sent"),
        ("invitation_resent", "Internal User Invitation Resent"),
        ("invitation_failed", "Internal User Invitation Failed"),
    ], required=True, readonly=True, index=True)
    event_at = fields.Datetime(required=True, readonly=True, index=True)
    actor_user_id = fields.Many2one(
        "res.users", required=True, readonly=True, ondelete="restrict", index=True,
    )
    target_user_id = fields.Many2one(
        "res.users", required=True, readonly=True, ondelete="restrict", index=True,
    )
    normalized_identity = fields.Char(required=True, readonly=True, index=True)
    prior_role = fields.Char(readonly=True)
    new_role = fields.Char(readonly=True)
    prior_reviewer = fields.Boolean(readonly=True)
    new_reviewer = fields.Boolean(readonly=True)
    prior_active = fields.Boolean(readonly=True)
    new_active = fields.Boolean(readonly=True)
    reused_identity = fields.Boolean(readonly=True)
    mail_id = fields.Many2one("mail.mail", readonly=True, ondelete="set null")
    mail_state = fields.Char(readonly=True)
    metadata = fields.Json(readonly=True)

    @api.model
    @api.private
    def _trucalc_log(
        self, event_type, actor, target, *, prior_role=False, new_role=False,
        prior_reviewer=False, new_reviewer=False, prior_active=False,
        new_active=False, reused_identity=False, mail=False, metadata=None,
    ):
        actor.ensure_one()
        target.ensure_one()
        if actor != self.env.user:
            raise AccessError(_("The internal user administration audit actor is invalid."))
        self.env["res.users"]._trucalc_require_internal_administrator()
        target = target.sudo().with_context(active_test=False).exists()
        if len(target) != 1:
            raise AccessError(_("The internal user administration audit target is invalid."))
        normalized = self.env["res.users"]._trucalc_normalize_internal_login(
            target.login
        )
        return super(TruCalcInternalUserAdminAudit, self.sudo()).create({
            "event_type": event_type,
            "event_at": fields.Datetime.now(),
            "actor_user_id": actor.id,
            "target_user_id": target.id,
            "normalized_identity": normalized,
            "prior_role": prior_role or False,
            "new_role": new_role or False,
            "prior_reviewer": bool(prior_reviewer),
            "new_reviewer": bool(new_reviewer),
            "prior_active": bool(prior_active),
            "new_active": bool(new_active),
            "reused_identity": bool(reused_identity),
            "mail_id": mail.id if mail else False,
            "mail_state": mail.state if mail else False,
            "metadata": metadata or {},
        })

    @api.model_create_multi
    def create(self, vals_list):
        raise AccessError(_("Internal user administration audit events are system controlled."))

    def write(self, vals):
        raise AccessError(_("Internal user administration audit events are immutable."))

    def unlink(self):
        raise AccessError(_("Internal user administration audit events cannot be deleted."))

    def copy(self, default=None):
        raise AccessError(_("Internal user administration audit events cannot be copied."))
