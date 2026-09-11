from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


ROLE_SELECTION = [
    ("administrator", "Bank Administrator"),
    ("requestor", "Bank Requestor"),
    ("view_only", "Bank View-Only"),
]


class TruCalcBankUserProvision(models.TransientModel):
    _name = "trucalc.bank.user.provision"
    _description = "Provision TruCalc Bank User"

    bank_company_id = fields.Many2one(
        "res.company", string="Bank", required=True, readonly=True,
        domain=[("trucalc_is_bank", "=", True), ("trucalc_bank_active", "=", True)],
    )
    name = fields.Char(required=True)
    login = fields.Char(string="Email / Login", required=True)
    role = fields.Selection(ROLE_SELECTION, required=True, default="requestor")
    active = fields.Boolean(default=True)

    @api.model
    def default_get(self, fields_list):
        self.env["res.company"]._trucalc_require_bank_administrator()
        values = super().default_get(fields_list)
        locked_bank_id = self.env.context.get("trucalc_locked_bank_company_id")
        default_bank_id = self.env.context.get("default_bank_company_id")
        if locked_bank_id and default_bank_id and locked_bank_id != default_bank_id:
            raise AccessError(_("The Bank provisioning context is invalid."))
        bank_id = locked_bank_id or default_bank_id
        if not bank_id and self.env.context.get("active_model") == "res.company":
            bank_id = self.env.context.get("active_id")
        if bank_id:
            bank = self.env["res.company"].browse(bank_id)._trucalc_bank_identity_record()
            values["bank_company_id"] = bank.id
        return values

    @api.model_create_multi
    def create(self, vals_list):
        self.env["res.company"]._trucalc_require_bank_administrator()
        locked_bank_id = self.env.context.get("trucalc_locked_bank_company_id")
        if locked_bank_id:
            bank = self.env["res.company"].browse(
                locked_bank_id
            )._trucalc_bank_identity_record()
            for values in vals_list:
                supplied_bank_id = values.get("bank_company_id")
                if supplied_bank_id and supplied_bank_id != bank.id:
                    raise AccessError(_("The Bank provisioning context is invalid."))
                values["bank_company_id"] = bank.id
        elif any(not values.get("bank_company_id") for values in vals_list):
            raise ValidationError(_(
                "Open Add Bank User from an active Bank so the Bank can be fixed automatically."
            ))
        return super().create(vals_list)

    def action_save(self):
        self.ensure_one()
        locked_bank_id = self.env.context.get("trucalc_locked_bank_company_id")
        if locked_bank_id and self.bank_company_id.id != locked_bank_id:
            raise AccessError(_("The Bank provisioning context is invalid."))
        user = self.env["res.users"]._trucalc_provision_bank_user(
            self.bank_company_id, self.name, self.login, self.role, self.active,
        )
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {
                "title": _("Bank User Created"),
                "message": _("%s was created without sending an invitation.") % user.name,
                "type": "success", "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }


class TruCalcBankUserRoleChange(models.TransientModel):
    _name = "trucalc.bank.user.role.change"
    _description = "Change TruCalc Bank User Role"

    bank_company_id = fields.Many2one("res.company", required=True, readonly=True)
    target_user_id = fields.Many2one("res.users", required=True, readonly=True)
    role = fields.Selection(ROLE_SELECTION, required=True)

    def action_save(self):
        self.ensure_one()
        self.target_user_id._trucalc_change_bank_role(self.bank_company_id, self.role)
        return {"type": "ir.actions.client", "tag": "reload"}


class TruCalcBankUserManagement(models.TransientModel):
    _name = "trucalc.bank.user.management"
    _description = "Managed TruCalc Bank User"
    _order = "id"

    owner_user_id = fields.Many2one("res.users", required=True, readonly=True)
    bank_company_id = fields.Many2one("res.company", string="Bank", required=True, readonly=True)
    target_user_id = fields.Many2one("res.users", required=True, readonly=True)
    name = fields.Char(compute="_compute_projection", compute_sudo=True)
    login = fields.Char(string="Email / Login", compute="_compute_projection", compute_sudo=True)
    role = fields.Selection(ROLE_SELECTION, compute="_compute_projection", compute_sudo=True)
    user_active = fields.Boolean(string="Active", compute="_compute_projection", compute_sudo=True)
    invitation_state = fields.Selection([
        ("not_invited", "Not Invited"),
        ("pending", "Invitation Pending"),
        ("confirmed", "Confirmed"),
        ("inactive", "Inactive"),
    ], compute="_compute_projection", compute_sudo=True)

    @api.depends("target_user_id")
    def _compute_projection(self):
        for row in self:
            user = row.target_user_id.sudo().with_context(active_test=False)
            row.name = user.name
            row.login = user.login
            row.role = user._trucalc_bank_role_key()
            row.user_active = user.active
            if not user.active:
                row.invitation_state = "inactive"
            elif user.partner_id.signup_type:
                row.invitation_state = "pending"
            elif user.login_date:
                row.invitation_state = "confirmed"
            else:
                row.invitation_state = "not_invited"

    @api.model
    @api.private
    def _trucalc_open(self, bank):
        actor = self.env["res.company"]._trucalc_require_bank_administrator()
        bank = bank._trucalc_bank_identity_record(require_active=False)
        users = self.env["res.users"].sudo().with_context(active_test=False).search([
            ("trucalc_bank_company_id", "=", bank.id),
        ])
        rows = super(TruCalcBankUserManagement, self.sudo()).create([
            {"owner_user_id": actor.id, "bank_company_id": bank.id, "target_user_id": user.id}
            for user in users
        ]) if users else self.browse()
        return {
            "type": "ir.actions.act_window", "name": _("Bank Users"),
            "res_model": self._name, "view_mode": "list",
            "views": [(self.env.ref("trucalc_orders.view_trucalc_bank_user_management_list").id, "list")],
            "domain": [("id", "in", rows.ids)],
            "context": {"default_bank_company_id": bank.id, "active_bank_company_id": bank.id},
        }

    @api.model_create_multi
    def create(self, vals_list):
        raise AccessError(_("Bank User management rows are system controlled."))

    def _controlled_target(self, require_active=True):
        self.ensure_one()
        actor = self.env["res.company"]._trucalc_require_bank_administrator()
        if self.owner_user_id != actor:
            raise AccessError(_("The Bank User management context is invalid."))
        return self.target_user_id._trucalc_assert_managed_bank_user(
            self.bank_company_id, require_active=require_active,
        )

    def action_change_role(self):
        target = self._controlled_target()
        return {
            "type": "ir.actions.act_window", "name": _("Change Bank Role"),
            "res_model": "trucalc.bank.user.role.change", "view_mode": "form",
            "target": "new", "context": {
                "default_bank_company_id": self.bank_company_id.id,
                "default_target_user_id": target.id,
                "default_role": target._trucalc_bank_role_key(),
            },
        }

    def action_deactivate(self):
        self._controlled_target()._trucalc_deactivate_bank_user(self.bank_company_id)
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_reactivate(self):
        self._controlled_target(require_active=False)._trucalc_reactivate_bank_user(self.bank_company_id)
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_send_invitation(self):
        result = self._controlled_target()._trucalc_send_bank_invitation(
            self.bank_company_id
        )
        if not result["sent"]:
            return {
                "type": "ir.actions.client", "tag": "display_notification",
                "params": {
                    "title": _("Invitation Delivery Failed"),
                    "message": _(
                        "Outbound invitation email %(mail_id)s was rendered and retained, "
                        "but delivery was not confirmed. Resend remains available."
                    ) % {"mail_id": result["mail_id"]},
                    "type": "danger", "sticky": True,
                    "next": {"type": "ir.actions.client", "tag": "reload"},
                },
            }
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {
                "title": _("Invitation Sent"),
                "message": _("The outbound invitation email was sent successfully."),
                "type": "success", "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }
