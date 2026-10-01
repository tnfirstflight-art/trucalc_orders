from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


INTERNAL_ROLE_SELECTION = [
    ("administrator", "Administrator"),
    ("operations", "Operations"),
]


class TruCalcInternalUserProvision(models.TransientModel):
    _name = "trucalc.internal.user.provision"
    _description = "Provision TruCalc Internal User"

    name = fields.Char(required=True)
    login = fields.Char(string="Email / Login", required=True)
    phone = fields.Char()
    role = fields.Selection(
        INTERNAL_ROLE_SELECTION, required=True, default="operations",
    )
    reviewer = fields.Boolean(string="Reviewer")

    @api.onchange("phone")
    def _onchange_phone(self):
        for wizard in self:
            wizard.phone = self.env[
                "res.users"
            ]._trucalc_format_internal_phone(wizard.phone)

    @api.model
    def default_get(self, fields_list):
        self.env["res.users"]._trucalc_require_internal_administrator()
        return super().default_get(fields_list)

    @api.model_create_multi
    def create(self, vals_list):
        self.env["res.users"]._trucalc_require_internal_administrator()
        return super().create(vals_list)

    def action_save(self):
        self.ensure_one()
        user = self.env["res.users"]._trucalc_provision_internal_user(
            self.name, self.login, self.phone, self.role, self.reviewer,
        )
        return self.env[
            "trucalc.internal.user.management"
        ]._trucalc_open_user(user)


class TruCalcInternalUserRoleChange(models.TransientModel):
    _name = "trucalc.internal.user.role.change"
    _description = "Change TruCalc Internal User Role"

    target_user_id = fields.Many2one(
        "res.users", required=True, readonly=True,
    )
    role = fields.Selection(INTERNAL_ROLE_SELECTION, required=True)

    @api.model_create_multi
    def create(self, vals_list):
        self.env["res.users"]._trucalc_require_internal_administrator()
        return super().create(vals_list)

    def action_save(self):
        self.ensure_one()
        self.target_user_id._trucalc_change_internal_role(self.role)
        return {"type": "ir.actions.client", "tag": "reload"}


class TruCalcInternalUserDeactivate(models.TransientModel):
    _name = "trucalc.internal.user.deactivate"
    _description = "Deactivate TruCalc Internal User"

    target_user_id = fields.Many2one(
        "res.users", required=True, readonly=True,
    )
    open_activity_count = fields.Integer(
        compute="_compute_open_activity_count", compute_sudo=True,
    )
    acknowledge_open_activities = fields.Boolean(
        string="I acknowledge that open activities will remain assigned"
    )

    @api.depends("target_user_id")
    def _compute_open_activity_count(self):
        for wizard in self:
            wizard.open_activity_count = (
                wizard.target_user_id._trucalc_open_activity_count()
                if wizard.target_user_id else 0
            )

    @api.model_create_multi
    def create(self, vals_list):
        self.env["res.users"]._trucalc_require_internal_administrator()
        return super().create(vals_list)

    def action_confirm(self):
        self.ensure_one()
        if self.open_activity_count and not self.acknowledge_open_activities:
            raise ValidationError(_(
                "Acknowledge the open activities before deactivation."
            ))
        self.target_user_id._trucalc_deactivate_internal_user(
            acknowledge_open_activities=self.acknowledge_open_activities,
        )
        return {"type": "ir.actions.client", "tag": "reload"}


class TruCalcInternalUserManagement(models.TransientModel):
    _name = "trucalc.internal.user.management"
    _description = "Managed TruCalc Internal User"
    _order = "id"

    owner_user_id = fields.Many2one("res.users", required=True, readonly=True)
    target_user_id = fields.Many2one("res.users", required=True, readonly=True)
    name = fields.Char(compute="_compute_projection", compute_sudo=True)
    login = fields.Char(
        string="Email / Login", compute="_compute_projection", compute_sudo=True,
    )
    phone = fields.Char(compute="_compute_projection", compute_sudo=True)
    role = fields.Selection(
        INTERNAL_ROLE_SELECTION, compute="_compute_projection", compute_sudo=True,
    )
    reviewer = fields.Boolean(
        string="Reviewer", compute="_compute_projection", compute_sudo=True,
    )
    user_active = fields.Boolean(
        string="Active", compute="_compute_projection", compute_sudo=True,
    )
    login_date = fields.Datetime(
        string="Last Login", compute="_compute_projection", compute_sudo=True,
    )
    setup_link_prepared = fields.Boolean(
        compute="_compute_projection", compute_sudo=True,
    )

    @api.depends("target_user_id")
    def _compute_projection(self):
        for row in self:
            user = row.target_user_id.sudo().with_context(active_test=False)
            row.name = user.name
            row.login = user.login
            row.phone = self.env[
                "res.users"
            ]._trucalc_format_internal_phone(user.phone)
            row.role = user._trucalc_internal_role_key()
            row.reviewer = bool(user._trucalc_persona_membership()["reviewer"])
            row.user_active = user.active
            row.login_date = user.login_date
            row.setup_link_prepared = bool(user.partner_id.signup_type)

    @api.model
    @api.private
    def _trucalc_users(self):
        roles = self.env["res.groups"].browse([
            group.id
            for group in self.env["res.users"]._trucalc_internal_role_map().values()
        ])
        candidates = self.env["res.users"].sudo().with_context(active_test=False).search([
            ("group_ids", "in", roles.ids),
        ])
        return candidates.filtered(lambda user: (
            len(user._trucalc_persona_membership()["internal"]) == 1
            and not user._trucalc_persona_membership()["bank"]
            and not user._trucalc_persona_membership()["vendor"]
            and not user.trucalc_bank_company_id
            and not user.trucalc_vendor_id
            and not user.share
        ))

    @api.model
    @api.private
    def _trucalc_open(self):
        actor = self.env["res.users"]._trucalc_require_internal_administrator()
        users = self._trucalc_users()
        rows = super(TruCalcInternalUserManagement, self.sudo()).create([
            {"owner_user_id": actor.id, "target_user_id": user.id}
            for user in users
        ]) if users else self.browse()
        return {
            "type": "ir.actions.act_window",
            "name": _("TruCalc Users"),
            "res_model": self._name,
            "view_mode": "list,form",
            "views": [
                (
                    self.env.ref(
                        "trucalc_orders.view_trucalc_internal_user_management_list"
                    ).id,
                    "list",
                ),
                (
                    self.env.ref(
                        "trucalc_orders.view_trucalc_internal_user_management_form"
                    ).id,
                    "form",
                ),
            ],
            "domain": [("id", "in", rows.ids)],
        }

    @api.model
    @api.private
    def _trucalc_open_user(self, user):
        actor = self.env["res.users"]._trucalc_require_internal_administrator()
        target = user._trucalc_assert_managed_internal_user(
            require_active=False, require_company_binding=False,
        )
        row = super(TruCalcInternalUserManagement, self.sudo()).create({
            "owner_user_id": actor.id,
            "target_user_id": target.id,
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("TruCalc User"),
            "res_model": self._name,
            "res_id": row.id,
            "view_mode": "form",
            "views": [(
                self.env.ref(
                    "trucalc_orders.view_trucalc_internal_user_management_form"
                ).id,
                "form",
            )],
            "target": "current",
        }

    @api.model_create_multi
    def create(self, vals_list):
        raise AccessError(_("TruCalc User management rows are system controlled."))

    def _controlled_target(self, require_active=True, require_company_binding=True):
        self.ensure_one()
        actor = self.env["res.users"]._trucalc_require_internal_administrator()
        if self.owner_user_id != actor:
            raise AccessError(_("The TruCalc User management context is invalid."))
        return self.target_user_id._trucalc_assert_managed_internal_user(
            require_active=require_active,
            require_company_binding=require_company_binding,
        )

    def action_change_role(self):
        target = self._controlled_target()
        return {
            "type": "ir.actions.act_window",
            "name": _("Change TruCalc Role"),
            "res_model": "trucalc.internal.user.role.change",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_target_user_id": target.id,
                "default_role": target._trucalc_internal_role_key(),
            },
        }

    def action_add_reviewer(self):
        self._controlled_target()._trucalc_set_internal_reviewer(True)
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_remove_reviewer(self):
        self._controlled_target()._trucalc_set_internal_reviewer(False)
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_deactivate(self):
        target = self._controlled_target()
        return {
            "type": "ir.actions.act_window",
            "name": _("Deactivate TruCalc User"),
            "res_model": "trucalc.internal.user.deactivate",
            "view_mode": "form",
            "target": "new",
            "context": {"default_target_user_id": target.id},
        }

    def action_reactivate(self):
        self._controlled_target(
            require_active=False, require_company_binding=False,
        )._trucalc_reactivate_internal_user()
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_send_invitation(self):
        result = self._controlled_target()._trucalc_send_internal_invitation()
        if not result["sent"]:
            return {
                "type": "ir.actions.client", "tag": "display_notification",
                "params": {
                    "title": _("Invitation Delivery Failed"),
                    "message": _(
                        "Outbound invitation email %(mail_id)s was retained, but "
                        "delivery was not confirmed. Resend remains available."
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

    def action_view_audit(self):
        target = self._controlled_target(
            require_active=False, require_company_binding=False,
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("TruCalc User Administration History"),
            "res_model": "trucalc.internal.user.admin.audit",
            "view_mode": "list,form",
            "views": [
                (
                    self.env.ref(
                        "trucalc_orders.view_trucalc_internal_user_admin_audit_list"
                    ).id,
                    "list",
                ),
                (
                    self.env.ref(
                        "trucalc_orders.view_trucalc_internal_user_admin_audit_form"
                    ).id,
                    "form",
                ),
            ],
            "domain": [("target_user_id", "=", target.id)],
        }
