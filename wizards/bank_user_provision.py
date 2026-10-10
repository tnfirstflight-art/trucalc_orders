from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError

from ..utils.phone import format_phone


ROLE_SELECTION = [
    ("administrator", "Bank Administrator"),
    ("requestor", "Bank Requestor"),
    ("view_only", "Bank View-Only"),
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


class TruCalcBankUserProvision(models.TransientModel):
    _name = "trucalc.bank.user.provision"
    _description = "Provision TruCalc Bank User"

    bank_company_id = fields.Many2one(
        "res.company", string="Bank", required=True, readonly=True,
        domain=[("trucalc_is_bank", "=", True), ("trucalc_bank_active", "=", True)],
    )
    name = fields.Char(required=True)
    login = fields.Char(string="Email / Login", required=True)
    phone = fields.Char(string="Phone Number")
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
            phone=self.phone,
        )
        return self.env["trucalc.bank.user.management"]._trucalc_open(
            user.trucalc_bank_company_id
        )


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


class TruCalcBankUserEdit(models.TransientModel):
    _name = "trucalc.bank.user.edit"
    _description = "Edit TruCalc Bank User"

    owner_user_id = fields.Many2one("res.users", required=True, readonly=True)
    bank_company_id = fields.Many2one("res.company", required=True, readonly=True)
    target_user_id = fields.Many2one("res.users", required=True, readonly=True)
    login = fields.Char(string="Email / Login", readonly=True)
    name = fields.Char(required=True)
    phone = fields.Char(string="Phone Number")

    _editable_fields = frozenset({"name", "phone"})

    @api.model
    def default_get(self, fields_list):
        actor = self.env["res.company"]._trucalc_require_bank_administrator()
        values = super().default_get(fields_list)
        values["owner_user_id"] = actor.id
        target_id = self.env.context.get("trucalc_locked_target_user_id")
        bank_id = self.env.context.get("trucalc_locked_bank_company_id")
        if target_id and bank_id:
            bank = self.env["res.company"].browse(
                bank_id
            )._trucalc_bank_identity_record(require_active=False)
            target = self.env["res.users"].browse(
                target_id
            )._trucalc_assert_managed_bank_user(bank)
            values.update({
                "bank_company_id": bank.id,
                "target_user_id": target.id,
                "login": target.login,
                "name": target.name,
                "phone": target.phone,
            })
        return values

    @api.model_create_multi
    def create(self, vals_list):
        actor = self.env["res.company"]._trucalc_require_bank_administrator()
        bank_id = self.env.context.get("trucalc_locked_bank_company_id")
        target_id = self.env.context.get("trucalc_locked_target_user_id")
        if not bank_id or not target_id:
            raise AccessError(_("The Bank User edit context is invalid."))
        bank = self.env["res.company"].browse(
            bank_id
        )._trucalc_bank_identity_record(require_active=False)
        target = self.env["res.users"].browse(
            target_id
        )._trucalc_assert_managed_bank_user(bank)
        for values in vals_list:
            if (
                values.get("bank_company_id", bank.id) != bank.id
                or values.get("target_user_id", target.id) != target.id
            ):
                raise AccessError(_("The Bank User edit context is invalid."))
            values.update({
                "owner_user_id": actor.id,
                "bank_company_id": bank.id,
                "target_user_id": target.id,
                "login": target.login,
            })
        return super().create(vals_list)

    def write(self, values):
        actor = self.env["res.company"]._trucalc_require_bank_administrator()
        if set(values) - self._editable_fields:
            raise AccessError(_("The Bank User edit context is invalid."))
        if any(wizard.owner_user_id != actor for wizard in self):
            raise AccessError(_("The Bank User edit context is invalid."))
        return super().write(values)

    def action_save(self):
        self.ensure_one()
        actor = self.env["res.company"]._trucalc_require_bank_administrator()
        locked_bank_id = self.env.context.get("trucalc_locked_bank_company_id")
        locked_target_id = self.env.context.get("trucalc_locked_target_user_id")
        if (
            self.owner_user_id != actor
            or not locked_bank_id or not locked_target_id
            or self.bank_company_id.id != locked_bank_id
            or self.target_user_id.id != locked_target_id
        ):
            raise AccessError(_("The Bank User edit context is invalid."))
        self.target_user_id._trucalc_edit_bank_identity(
            self.bank_company_id, self.name, self.phone,
        )
        return self.env["trucalc.bank.user.management"]._trucalc_open(
            self.bank_company_id
        )


class TruCalcBankUserEmailChange(models.TransientModel):
    _name = "trucalc.bank.user.email.change"
    _description = "Change TruCalc Bank User Email"

    owner_user_id = fields.Many2one("res.users", required=True, readonly=True)
    bank_company_id = fields.Many2one("res.company", required=True, readonly=True)
    target_user_id = fields.Many2one("res.users", required=True, readonly=True)
    current_login = fields.Char(string="Current Email / Login", readonly=True)
    new_login = fields.Char(string="New Email / Login", required=True)
    pending_invitation = fields.Boolean(readonly=True)
    acknowledge_invitation_invalidation = fields.Boolean(
        string="I understand that a new invitation must be sent",
    )

    _editable_fields = frozenset({
        "new_login", "acknowledge_invitation_invalidation",
    })

    @api.model
    def default_get(self, fields_list):
        actor = self.env["res.company"]._trucalc_require_bank_administrator()
        values = super().default_get(fields_list)
        target_id = self.env.context.get("trucalc_locked_target_user_id")
        bank_id = self.env.context.get("trucalc_locked_bank_company_id")
        if not target_id or not bank_id:
            raise AccessError(_("The Bank User email-change context is invalid."))
        bank = self.env["res.company"].browse(
            bank_id
        )._trucalc_bank_identity_record(require_active=False)
        target = self.env["res.users"].browse(
            target_id
        )._trucalc_assert_managed_bank_user(bank)
        values.update({
            "owner_user_id": actor.id,
            "bank_company_id": bank.id,
            "target_user_id": target.id,
            "current_login": target.login,
            "pending_invitation": bool(target.partner_id.signup_type),
        })
        return values

    @api.model_create_multi
    def create(self, vals_list):
        actor = self.env["res.company"]._trucalc_require_bank_administrator()
        bank_id = self.env.context.get("trucalc_locked_bank_company_id")
        target_id = self.env.context.get("trucalc_locked_target_user_id")
        if not bank_id or not target_id:
            raise AccessError(_("The Bank User email-change context is invalid."))
        bank = self.env["res.company"].browse(
            bank_id
        )._trucalc_bank_identity_record(require_active=False)
        target = self.env["res.users"].browse(
            target_id
        )._trucalc_assert_managed_bank_user(bank)
        for values in vals_list:
            if (
                values.get("bank_company_id", bank.id) != bank.id
                or values.get("target_user_id", target.id) != target.id
                or values.get("owner_user_id", actor.id) != actor.id
            ):
                raise AccessError(_("The Bank User email-change context is invalid."))
            values.update({
                "owner_user_id": actor.id,
                "bank_company_id": bank.id,
                "target_user_id": target.id,
                "current_login": target.login,
                "pending_invitation": bool(target.partner_id.signup_type),
            })
        return super().create(vals_list)

    def write(self, values):
        actor = self.env["res.company"]._trucalc_require_bank_administrator()
        if set(values) - self._editable_fields:
            raise AccessError(_("The Bank User email-change context is invalid."))
        if any(wizard.owner_user_id != actor for wizard in self):
            raise AccessError(_("The Bank User email-change context is invalid."))
        return super().write(values)

    def action_save(self):
        self.ensure_one()
        actor = self.env["res.company"]._trucalc_require_bank_administrator()
        locked_bank_id = self.env.context.get("trucalc_locked_bank_company_id")
        locked_target_id = self.env.context.get("trucalc_locked_target_user_id")
        if (
            self.owner_user_id != actor
            or not locked_bank_id or not locked_target_id
            or self.bank_company_id.id != locked_bank_id
            or self.target_user_id.id != locked_target_id
        ):
            raise AccessError(_("The Bank User email-change context is invalid."))
        target = self.target_user_id._trucalc_assert_managed_bank_user(
            self.bank_company_id
        )
        if (
            target.partner_id.signup_type
            and not self.acknowledge_invitation_invalidation
        ):
            raise ValidationError(_(
                "Confirm that the pending invitation will be invalidated and a "
                "new invitation must be sent."
            ))
        target._trucalc_change_bank_email(self.bank_company_id, self.new_login)
        return self.env["trucalc.bank.user.management"]._trucalc_open(
            self.bank_company_id
        )


class TruCalcBankUserManage(models.TransientModel):
    """One secured Bank-only surface for profile and account administration."""

    _name = "trucalc.bank.user.manage"
    _description = "Manage TruCalc Bank User"

    owner_user_id = fields.Many2one("res.users", required=True, readonly=True)
    management_row_id = fields.Many2one(
        "trucalc.bank.user.management", required=True, readonly=True,
        ondelete="cascade",
    )
    bank_company_id = fields.Many2one(
        "res.company", string="Bank", required=True, readonly=True,
    )
    target_user_id = fields.Many2one(
        "res.users", string="Bank User", required=True, readonly=True,
    )
    name = fields.Char(required=True)
    login = fields.Char(string="Email / Login", required=True)
    phone = fields.Char(string="Phone Number")
    role = fields.Selection(ROLE_SELECTION, required=True)
    user_active = fields.Boolean(
        string="Active", compute="_compute_account_projection", compute_sudo=True,
    )
    account_state = fields.Selection(
        ACCOUNT_STATE_SELECTION, string="Account State",
        compute="_compute_account_projection", compute_sudo=True,
    )
    last_login = fields.Datetime(
        compute="_compute_account_projection", compute_sudo=True,
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
    account_audit_ids = fields.Many2many(
        "trucalc.bank.admin.audit", string="Account History",
        compute="_compute_account_projection", compute_sudo=True,
    )
    email_changed = fields.Boolean(compute="_compute_change_flags")
    pending_account_link = fields.Boolean(
        compute="_compute_change_flags", compute_sudo=True,
    )
    requires_link_acknowledgement = fields.Boolean(
        compute="_compute_change_flags", compute_sudo=True,
    )
    acknowledge_account_link_invalidation = fields.Boolean(
        string="I understand that the current account link will be invalidated",
    )

    _editable_fields = frozenset({
        "name", "login", "phone", "role",
        "acknowledge_account_link_invalidation",
    })

    @api.depends("target_user_id", "bank_company_id")
    def _compute_account_projection(self):
        Audit = self.env["trucalc.bank.admin.audit"].sudo()
        for wizard in self:
            target = wizard.target_user_id.sudo().with_context(active_test=False)
            bank = wizard.bank_company_id.sudo()
            if not target or not bank:
                wizard.user_active = False
                wizard.account_state = False
                wizard.last_login = False
                wizard.recent_email_kind = False
                wizard.recent_email_outcome = False
                wizard.recent_email_at = False
                wizard.recent_mail_state = False
                wizard.account_audit_ids = False
                continue
            wizard.user_active = target.active
            wizard.account_state = target._trucalc_bank_account_state(bank)
            wizard.last_login = target.login_date
            history = Audit.search([
                ("bank_company_id", "=", bank.id),
                ("target_user_id", "=", target.id),
            ], order="event_at desc, id desc", limit=20)
            wizard.account_audit_ids = history
            latest = Audit.search([
                ("bank_company_id", "=", bank.id),
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
        for wizard in self:
            target = wizard.target_user_id.sudo().with_context(active_test=False)
            normalized = False
            if wizard.login:
                try:
                    normalized = self.env["res.users"]._trucalc_normalize_login(
                        wizard.login
                    )
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
        actor = self.env["res.company"]._trucalc_require_bank_administrator()
        row_id = self.env.context.get("trucalc_bank_user_management_row_id")
        if not row_id:
            raise AccessError(_("The Bank User management context is invalid."))
        row = self.env["trucalc.bank.user.management"].browse(row_id)
        target = row._controlled_target(require_active=False)
        for values in vals_list:
            for field_name, expected in (
                ("owner_user_id", actor.id),
                ("management_row_id", row.id),
                ("bank_company_id", row.bank_company_id.id),
                ("target_user_id", target.id),
            ):
                if values.get(field_name, expected) != expected:
                    raise AccessError(_("The Bank User management context is invalid."))
            values.update({
                "owner_user_id": actor.id,
                "management_row_id": row.id,
                "bank_company_id": row.bank_company_id.id,
                "target_user_id": target.id,
                "name": target.name,
                "login": target.login,
                "phone": format_phone(target.phone),
                "role": target._trucalc_bank_role_key(),
            })
        return super().create(vals_list)

    def write(self, values):
        actor = self.env["res.company"]._trucalc_require_bank_administrator()
        if set(values) - self._editable_fields:
            raise AccessError(_("The Bank User management context is invalid."))
        if any(wizard.owner_user_id != actor for wizard in self):
            raise AccessError(_("The Bank User management context is invalid."))
        return super().write(values)

    def _controlled_target(self, require_active=True):
        self.ensure_one()
        actor = self.env["res.company"]._trucalc_require_bank_administrator()
        row = self.management_row_id
        row_target = row._controlled_target(require_active=require_active)
        if (
            self.owner_user_id != actor
            or row.owner_user_id != actor
            or row.bank_company_id != self.bank_company_id
            or row.target_user_id != self.target_user_id
            or row_target != self.target_user_id
        ):
            raise AccessError(_("The Bank User management context is invalid."))
        return row_target

    def _profile_changes(self, target):
        self.ensure_one()
        normalized = target._trucalc_normalize_login(self.login)
        clean_name = " ".join((self.name or "").split())
        clean_phone = target._trucalc_clean_bank_phone(self.phone)
        if not clean_name:
            raise ValidationError(_("Bank User Name is required."))
        if self.role not in target._trucalc_bank_role_map():
            raise ValidationError(_("Select one Bank role."))
        return {
            "identity": (
                clean_name != target.name
                or clean_phone != format_phone(target.phone)
            ),
            "email": normalized != target.login,
            "role": self.role != target._trucalc_bank_role_key(),
            "clean_name": clean_name,
            "clean_phone": clean_phone,
            "normalized": normalized,
        }

    def _assert_no_unsaved_changes(self, target):
        changes = self._profile_changes(target)
        if any(changes[key] for key in ("identity", "email", "role")):
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
            "identity:%s" % target._trucalc_normalize_login(target.login),
            "identity:%s" % changes["normalized"],
        )
        target = target._trucalc_lock_target()._trucalc_assert_managed_bank_user(
            self.bank_company_id
        )
        with self.env.cr.savepoint():
            if changes["identity"]:
                target._trucalc_edit_bank_identity(
                    self.bank_company_id,
                    changes["clean_name"], changes["clean_phone"],
                )
            if changes["email"]:
                target._trucalc_change_bank_email(
                    self.bank_company_id, changes["normalized"],
                )
            if changes["role"]:
                target._trucalc_change_bank_role(self.bank_company_id, self.role)
            target.invalidate_recordset()
            target.partner_id.invalidate_recordset()
            target._trucalc_assert_managed_bank_user(self.bank_company_id)
            if (
                target.name != changes["clean_name"]
                or target.login != changes["normalized"]
                or target.email != changes["normalized"]
                or format_phone(target.phone) != changes["clean_phone"]
                or target._trucalc_bank_role_key() != self.role
            ):
                raise ValidationError(_(
                    "The Bank User changes did not complete safely."
                ))
        return self.env["trucalc.bank.user.management"]._trucalc_open(
            self.bank_company_id
        )

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
                    "trucalc.bank.user.management"
                ]._trucalc_open(self.bank_company_id),
            },
        }

    def action_send_invitation(self):
        target = self._controlled_target()
        self._assert_no_unsaved_changes(target)
        result = target._trucalc_send_bank_invitation(self.bank_company_id)
        return self._account_notification(result, _("Invitation"))

    def action_resend_invitation(self):
        target = self._controlled_target()
        self._assert_no_unsaved_changes(target)
        result = target._trucalc_resend_bank_invitation(self.bank_company_id)
        return self._account_notification(result, _("Invitation"))

    def action_send_password_reset(self):
        target = self._controlled_target()
        self._assert_no_unsaved_changes(target)
        result = target._trucalc_send_bank_password_reset(self.bank_company_id)
        return self._account_notification(result, _("Password Reset"))

    def action_deactivate(self):
        target = self._controlled_target()
        self._assert_no_unsaved_changes(target)
        target._trucalc_deactivate_bank_user(self.bank_company_id)
        return self.env["trucalc.bank.user.management"]._trucalc_open(
            self.bank_company_id
        )

    def action_reactivate(self):
        target = self._controlled_target(require_active=False)
        self._assert_no_unsaved_changes(target)
        target._trucalc_reactivate_bank_user(self.bank_company_id)
        return self.env["trucalc.bank.user.management"]._trucalc_open(
            self.bank_company_id
        )


class TruCalcBankUserManagement(models.TransientModel):
    _name = "trucalc.bank.user.management"
    _description = "Managed TruCalc Bank User"
    _order = "id"

    owner_user_id = fields.Many2one("res.users", required=True, readonly=True)
    bank_company_id = fields.Many2one("res.company", string="Bank", required=True, readonly=True)
    target_user_id = fields.Many2one("res.users", required=True, readonly=True)
    name = fields.Char(compute="_compute_projection", compute_sudo=True)
    login = fields.Char(string="Email / Login", compute="_compute_projection", compute_sudo=True)
    phone = fields.Char(string="Phone Number", compute="_compute_projection", compute_sudo=True)
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
            row.phone = user.phone
            row.role = user._trucalc_bank_role_key()
            row.user_active = user.active
            state = user._trucalc_bank_account_state(row.bank_company_id)
            if state == "inactive":
                row.invitation_state = "inactive"
            elif state in ("invitation_pending", "invitation_expired"):
                row.invitation_state = "pending"
            elif state in ("confirmed", "reset_pending"):
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

    def action_manage(self):
        target = self._controlled_target(require_active=False)
        context = {
            "trucalc_bank_user_management_row_id": self.id,
            "trucalc_locked_bank_company_id": self.bank_company_id.id,
            "trucalc_locked_target_user_id": target.id,
        }
        wizard = self.env["trucalc.bank.user.manage"].with_context(
            **context
        ).create({})
        return {
            "type": "ir.actions.act_window",
            "name": _("Manage Bank User"),
            "res_model": "trucalc.bank.user.manage",
            "res_id": wizard.id,
            "view_mode": "form",
            "views": [(
                self.env.ref(
                    "trucalc_orders.view_trucalc_bank_user_manage_form"
                ).id,
                "form",
            )],
            "target": "new",
            "context": context,
        }

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

    def action_edit(self):
        target = self._controlled_target()
        return {
            "type": "ir.actions.act_window", "name": _("Edit Bank User"),
            "res_model": "trucalc.bank.user.edit", "view_mode": "form",
            "views": [(
                self.env.ref(
                    "trucalc_orders.view_trucalc_bank_user_edit_form"
                ).id,
                "form",
            )],
            "target": "new", "context": {
                "default_bank_company_id": self.bank_company_id.id,
                "default_target_user_id": target.id,
                "default_login": target.login,
                "default_name": target.name,
                "default_phone": target.phone,
                "trucalc_locked_bank_company_id": self.bank_company_id.id,
                "trucalc_locked_target_user_id": target.id,
            },
        }

    def action_change_email(self):
        target = self._controlled_target()
        return {
            "type": "ir.actions.act_window", "name": _("Change Bank User Email"),
            "res_model": "trucalc.bank.user.email.change", "view_mode": "form",
            "views": [(
                self.env.ref(
                    "trucalc_orders.view_trucalc_bank_user_email_change_form"
                ).id,
                "form",
            )],
            "target": "new", "context": {
                "default_bank_company_id": self.bank_company_id.id,
                "default_target_user_id": target.id,
                "trucalc_locked_bank_company_id": self.bank_company_id.id,
                "trucalc_locked_target_user_id": target.id,
            },
        }

    def action_deactivate(self):
        self._controlled_target()._trucalc_deactivate_bank_user(self.bank_company_id)
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_reactivate(self):
        self._controlled_target(require_active=False)._trucalc_reactivate_bank_user(self.bank_company_id)
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_send_invitation(self):
        target = self._controlled_target()
        state = target._trucalc_bank_account_state(self.bank_company_id)
        result = (
            target._trucalc_resend_bank_invitation(self.bank_company_id)
            if state in ("invitation_pending", "invitation_expired")
            else target._trucalc_send_bank_invitation(self.bank_company_id)
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
                        "Outbound invitation email %(mail_id)s was retained, but delivery "
                        "failed. Resend remains available."
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
