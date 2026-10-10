from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


INTERNAL_ROLE_SELECTION = [
    ("administrator", "Administrator"),
    ("operations", "Operations"),
]

ACCOUNT_STATE_SELECTION = [
    ("never_invited", "Never Invited"),
    ("invitation_pending", "Invitation Pending"),
    ("invitation_expired", "Invitation Expired"),
    ("confirmed", "Confirmed"),
    ("reset_pending", "Password Reset Pending"),
    ("inactive", "Inactive"),
]

ACCOUNT_EMAIL_EVENTS = (
    "invitation_sent", "invitation_resent", "invitation_queued",
    "invitation_resend_queued", "invitation_failed",
    "invitation_resend_failed", "password_reset_sent",
    "password_reset_queued", "password_reset_failed",
)


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


class TruCalcInternalUserManage(models.TransientModel):
    """One secured surface for internal profile and account administration."""

    _name = "trucalc.internal.user.manage"
    _description = "Manage TruCalc Internal User"

    owner_user_id = fields.Many2one("res.users", required=True, readonly=True)
    management_row_id = fields.Many2one(
        "trucalc.internal.user.management", required=True, readonly=True,
        ondelete="cascade",
    )
    internal_company_id = fields.Many2one(
        "res.company", string="Internal Company", required=True, readonly=True,
    )
    target_user_id = fields.Many2one(
        "res.users", string="TruCalc User", required=True, readonly=True,
    )
    name = fields.Char(required=True)
    login = fields.Char(string="Email / Login", required=True)
    phone = fields.Char(string="Phone Number")
    role = fields.Selection(INTERNAL_ROLE_SELECTION, required=True)
    reviewer = fields.Boolean(string="Reviewer")
    user_active = fields.Boolean(
        string="Active", compute="_compute_account_projection", compute_sudo=True,
    )
    account_state = fields.Selection(
        ACCOUNT_STATE_SELECTION, string="Account State",
        compute="_compute_account_projection", compute_sudo=True,
    )
    last_login = fields.Datetime(
        string="Last Login", compute="_compute_account_projection", compute_sudo=True,
    )
    recent_email_kind = fields.Selection([
        ("invitation", "Invitation"), ("reset", "Password Reset"),
    ], compute="_compute_account_projection", compute_sudo=True)
    recent_email_outcome = fields.Selection([
        ("sent", "Sent"), ("queued", "Queued"), ("failed", "Failed"),
    ], compute="_compute_account_projection", compute_sudo=True)
    recent_email_at = fields.Datetime(
        compute="_compute_account_projection", compute_sudo=True,
    )
    recent_mail_state = fields.Char(
        string="Mail State", compute="_compute_account_projection",
        compute_sudo=True,
    )
    open_activity_count = fields.Integer(
        string="Open Activities", compute="_compute_account_projection",
        compute_sudo=True,
    )
    account_audit_ids = fields.Many2many(
        "trucalc.internal.user.admin.audit", string="Administration History",
        compute="_compute_account_projection", compute_sudo=True,
    )
    email_changed = fields.Boolean(
        compute="_compute_change_flags", compute_sudo=True,
    )
    pending_account_link = fields.Boolean(
        compute="_compute_change_flags", compute_sudo=True,
    )
    requires_link_acknowledgement = fields.Boolean(
        compute="_compute_change_flags", compute_sudo=True,
    )
    acknowledge_account_link_invalidation = fields.Boolean(
        string="I understand that the current account link will be invalidated",
    )
    acknowledge_open_activities = fields.Boolean(
        string="I acknowledge that open activities will remain assigned",
    )

    _editable_fields = frozenset({
        "name", "login", "phone", "role", "reviewer",
        "acknowledge_account_link_invalidation",
        "acknowledge_open_activities",
    })

    @api.depends("target_user_id")
    def _compute_account_projection(self):
        Audit = self.env["trucalc.internal.user.admin.audit"].sudo()
        for wizard in self:
            target = wizard.target_user_id.sudo().with_context(active_test=False)
            if not target:
                wizard.user_active = False
                wizard.account_state = False
                wizard.last_login = False
                wizard.recent_email_kind = False
                wizard.recent_email_outcome = False
                wizard.recent_email_at = False
                wizard.recent_mail_state = False
                wizard.open_activity_count = 0
                wizard.account_audit_ids = False
                continue
            wizard.user_active = target.active
            wizard.account_state = target._trucalc_internal_account_state()
            wizard.last_login = target.login_date
            wizard.open_activity_count = target._trucalc_open_activity_count()
            history = Audit.search([
                ("target_user_id", "=", target.id),
            ], order="event_at desc, id desc", limit=20)
            wizard.account_audit_ids = history
            latest = Audit.search([
                ("target_user_id", "=", target.id),
                ("event_type", "in", ACCOUNT_EMAIL_EVENTS),
            ], order="event_at desc, id desc", limit=1)
            metadata = (latest.metadata or {}) if latest else {}
            wizard.recent_email_kind = metadata.get("link_kind") or False
            wizard.recent_email_outcome = metadata.get("delivery_outcome") or False
            wizard.recent_email_at = latest.event_at if latest else False
            wizard.recent_mail_state = latest.mail_state if latest else False

    @api.depends("login", "target_user_id")
    def _compute_change_flags(self):
        Users = self.env["res.users"]
        for wizard in self:
            target = wizard.target_user_id.sudo().with_context(active_test=False)
            normalized = False
            if wizard.login:
                try:
                    normalized = Users._trucalc_normalize_internal_login(wizard.login)
                except ValidationError:
                    normalized = wizard.login.strip().casefold()
            current = (target.login or "").strip().casefold() if target else False
            wizard.email_changed = bool(target and normalized != current)
            wizard.pending_account_link = bool(
                target and target.partner_id.sudo().signup_type
            )
            wizard.requires_link_acknowledgement = bool(
                wizard.email_changed and wizard.pending_account_link
            )

    @api.model_create_multi
    def create(self, vals_list):
        actor = self.env["res.users"]._trucalc_require_internal_administrator()
        row_id = self.env.context.get("trucalc_internal_user_management_row_id")
        if not row_id:
            raise AccessError(_("The TruCalc User management context is invalid."))
        row = self.env["trucalc.internal.user.management"].browse(row_id)
        target = row._controlled_target(
            require_active=False, require_company_binding=False,
        )
        main_company = self.env.ref("base.main_company")
        for values in vals_list:
            for field_name, expected in (
                ("owner_user_id", actor.id),
                ("management_row_id", row.id),
                ("internal_company_id", main_company.id),
                ("target_user_id", target.id),
            ):
                if values.get(field_name, expected) != expected:
                    raise AccessError(_(
                        "The TruCalc User management context is invalid."
                    ))
            values.update({
                "owner_user_id": actor.id,
                "management_row_id": row.id,
                "internal_company_id": main_company.id,
                "target_user_id": target.id,
                "name": target.name,
                "login": target.login,
                "phone": target._trucalc_format_internal_phone(target.phone),
                "role": target._trucalc_internal_role_key(),
                "reviewer": bool(
                    target._trucalc_persona_membership()["reviewer"]
                ),
            })
        return super().create(vals_list)

    def write(self, values):
        actor = self.env["res.users"]._trucalc_require_internal_administrator()
        if set(values) - self._editable_fields:
            raise AccessError(_("The TruCalc User management context is invalid."))
        if any(wizard.owner_user_id != actor for wizard in self):
            raise AccessError(_("The TruCalc User management context is invalid."))
        return super().write(values)

    def _controlled_target(self, require_active=True, require_company_binding=True):
        self.ensure_one()
        actor = self.env["res.users"]._trucalc_require_internal_administrator()
        row = self.management_row_id
        row_target = row._controlled_target(
            require_active=require_active,
            require_company_binding=require_company_binding,
        )
        if (
            self.owner_user_id != actor
            or row.owner_user_id != actor
            or self.internal_company_id != self.env.ref("base.main_company")
            or row.target_user_id != self.target_user_id
            or row_target != self.target_user_id
        ):
            raise AccessError(_("The TruCalc User management context is invalid."))
        return row_target

    def _profile_changes(self, target):
        self.ensure_one()
        normalized = target._trucalc_normalize_internal_login(self.login)
        clean_name = " ".join((self.name or "").split())
        clean_phone = target._trucalc_format_internal_phone(self.phone)
        if not clean_name:
            raise ValidationError(_("TruCalc User Name is required."))
        if self.role not in target._trucalc_internal_role_map():
            raise ValidationError(_("Select one TruCalc internal role."))
        return {
            "identity": (
                clean_name != target.name
                or clean_phone != target._trucalc_format_internal_phone(target.phone)
            ),
            "email": normalized != target.login,
            "role": self.role != target._trucalc_internal_role_key(),
            "reviewer": self.reviewer != bool(
                target._trucalc_persona_membership()["reviewer"]
            ),
            "clean_name": clean_name,
            "clean_phone": clean_phone,
            "normalized": normalized,
        }

    def _assert_no_unsaved_changes(self, target):
        changes = self._profile_changes(target)
        if any(changes[key] for key in ("identity", "email", "role", "reviewer")):
            raise ValidationError(_(
                "Save or discard profile changes before using an account action."
            ))

    def action_save(self):
        self.ensure_one()
        target = self._controlled_target()
        changes = self._profile_changes(target)
        if (
            changes["email"] and target.partner_id.sudo().signup_type
            and not self.acknowledge_account_link_invalidation
        ):
            raise ValidationError(_(
                "Confirm that the current invitation or password-reset link will "
                "be invalidated before changing the email."
            ))
        target._trucalc_advisory_lock(
            "identity:%s" % target._trucalc_normalize_internal_login(target.login),
            "identity:%s" % changes["normalized"],
        )
        target = target._trucalc_lock_target()._trucalc_assert_managed_internal_user()
        with self.env.cr.savepoint():
            if changes["identity"]:
                target._trucalc_edit_internal_identity(
                    changes["clean_name"], changes["clean_phone"],
                )
            if changes["email"]:
                target._trucalc_change_internal_email(changes["normalized"])
            if changes["role"]:
                target._trucalc_change_internal_role(self.role)
            if changes["reviewer"]:
                target._trucalc_set_internal_reviewer(self.reviewer)
            target.invalidate_recordset()
            target.partner_id.invalidate_recordset()
            target._trucalc_assert_managed_internal_user()
            if (
                target.name != changes["clean_name"]
                or target.login != changes["normalized"]
                or target.email != changes["normalized"]
                or target._trucalc_format_internal_phone(target.phone)
                != changes["clean_phone"]
                or target._trucalc_internal_role_key() != self.role
                or bool(target._trucalc_persona_membership()["reviewer"])
                != self.reviewer
            ):
                raise ValidationError(_(
                    "The TruCalc User changes did not complete safely."
                ))
        return self.env["trucalc.internal.user.management"]._trucalc_open()

    def _account_notification(self, result, label):
        if result["queued"]:
            title = _("%(label)s Queued", label=label)
            message = _(
                "The account email is queued; delivery is not yet confirmed."
            )
            notification_type = "warning"
            sticky = True
        elif not result["sent"]:
            title = _("%(label)s Failed", label=label)
            message = _(
                "Outbound account email %(mail_id)s was retained, but delivery "
                "failed. The action remains available for retry.",
                mail_id=result["mail_id"],
            )
            notification_type = "danger"
            sticky = True
        else:
            title = _("%(label)s Sent", label=label)
            message = _("The outbound account email was sent successfully.")
            notification_type = "success"
            sticky = False
        return {
            "type": "ir.actions.client", "tag": "display_notification",
            "params": {
                "title": title, "message": message,
                "type": notification_type, "sticky": sticky,
                "next": self.env[
                    "trucalc.internal.user.management"
                ]._trucalc_open(),
            },
        }

    def action_send_invitation(self):
        target = self._controlled_target()
        self._assert_no_unsaved_changes(target)
        result = target._trucalc_send_internal_invitation()
        return self._account_notification(result, _("Invitation"))

    def action_resend_invitation(self):
        target = self._controlled_target()
        self._assert_no_unsaved_changes(target)
        result = target._trucalc_resend_internal_invitation()
        return self._account_notification(result, _("Invitation"))

    def action_send_password_reset(self):
        target = self._controlled_target()
        self._assert_no_unsaved_changes(target)
        result = target._trucalc_send_internal_password_reset()
        return self._account_notification(result, _("Password Reset"))

    def action_deactivate(self):
        target = self._controlled_target()
        self._assert_no_unsaved_changes(target)
        target._trucalc_deactivate_internal_user(
            acknowledge_open_activities=self.acknowledge_open_activities,
        )
        return self.env["trucalc.internal.user.management"]._trucalc_open()

    def action_reactivate(self):
        target = self._controlled_target(
            require_active=False, require_company_binding=False,
        )
        self._assert_no_unsaved_changes(target)
        target._trucalc_reactivate_internal_user()
        return self.env["trucalc.internal.user.management"]._trucalc_open()


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
    account_state = fields.Selection(
        ACCOUNT_STATE_SELECTION, string="Account State",
        compute="_compute_projection", compute_sudo=True,
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
            row.account_state = user._trucalc_internal_account_state()
            row.setup_link_prepared = row.account_state in (
                "invitation_pending", "invitation_expired",
            )

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
            "view_mode": "list",
            "views": [(
                self.env.ref(
                    "trucalc_orders.view_trucalc_internal_user_management_list"
                ).id,
                "list",
            )],
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
        return row.with_user(actor).action_manage()

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

    def action_manage(self):
        target = self._controlled_target(
            require_active=False, require_company_binding=False,
        )
        context = {
            "trucalc_internal_user_management_row_id": self.id,
            "trucalc_locked_target_user_id": target.id,
        }
        wizard = self.env["trucalc.internal.user.manage"].with_context(
            **context
        ).create({})
        return {
            "type": "ir.actions.act_window",
            "name": _("Manage TruCalc User"),
            "res_model": "trucalc.internal.user.manage",
            "res_id": wizard.id,
            "view_mode": "form",
            "views": [(
                self.env.ref(
                    "trucalc_orders.view_trucalc_internal_user_manage_form"
                ).id,
                "form",
            )],
            "target": "new",
            "context": context,
        }

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
        target = self._controlled_target()
        state = target._trucalc_internal_account_state()
        result = (
            target._trucalc_resend_internal_invitation()
            if state in ("invitation_pending", "invitation_expired")
            else target._trucalc_send_internal_invitation()
        )
        if result["queued"]:
            return {
                "type": "ir.actions.client", "tag": "display_notification",
                "params": {
                    "title": _("Invitation Queued"),
                    "message": _("The invitation is queued; delivery is not yet confirmed."),
                    "type": "warning", "sticky": True,
                    "next": {"type": "ir.actions.client", "tag": "reload"},
                },
            }
        if not result["sent"]:
            return {
                "type": "ir.actions.client", "tag": "display_notification",
                "params": {
                    "title": _("Invitation Delivery Failed"),
                    "message": _(
                        "Outbound invitation email %(mail_id)s was retained, but "
                        "delivery failed. Resend remains available."
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
