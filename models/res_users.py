import logging

from odoo import Command, api, fields, models, tools, _
from odoo.exceptions import AccessError, UserError, ValidationError


_logger = logging.getLogger(__name__)


class ResUsers(models.Model):
    _inherit = "res.users"

    trucalc_bank_company_id = fields.Many2one(
        "res.company",
        string="TruCalc Authorized Bank",
        index=True,
        help="Bank this user is authorized to represent in TruCalc.",
    )

    trucalc_vendor_id = fields.Many2one(
        "trucalc.vendor",
        string="TruCalc Vendor Organization",
        index=True,
        help="Vendor organization this user is authorized to represent in TruCalc.",
    )

    @api.model
    @api.private
    def _trucalc_bank_role_map(self):
        return {
            "administrator": self.env.ref("trucalc_orders.group_bank_admin"),
            "requestor": self.env.ref("trucalc_orders.group_bank_requestor"),
            "view_only": self.env.ref("trucalc_orders.group_bank_view_only"),
        }

    @api.private
    def _trucalc_bank_role_key(self):
        self.ensure_one()
        memberships = self._trucalc_persona_membership()["bank"]
        for key, group in self._trucalc_bank_role_map().items():
            if group in memberships:
                return key
        return False

    @api.model
    @api.private
    def _trucalc_normalize_login(self, value):
        normalized = tools.email_normalize(value or "")
        if not normalized or normalized != (value or "").strip().casefold():
            raise ValidationError(_("Enter one valid email address for the Bank user."))
        return normalized

    @api.model
    @api.private
    def _trucalc_identity_collision(self):
        return ValidationError(_(
            "This email cannot be provisioned automatically. Resolve the identity "
            "through controlled administration before trying again."
        ))

    @api.model
    @api.private
    def _trucalc_find_bank_user_identity(self, normalized):
        Users = self.sudo().with_context(active_test=False)
        Partners = self.env["res.partner"].sudo().with_context(active_test=False)
        users = Users.search([
            "|", ("login", "=ilike", normalized),
            ("partner_id.email", "=ilike", normalized),
        ]).filtered(lambda item: (
            tools.email_normalize(item.login or "") == normalized
            or tools.email_normalize(item.partner_id.email or "") == normalized
        ))
        partners = Partners.search([("email", "=ilike", normalized)]).filtered(
            lambda item: tools.email_normalize(item.email or "") == normalized
        ) | users.partner_id
        if len(users) > 1 or len(partners) > 1:
            raise self._trucalc_identity_collision()
        if users and (
            not partners
            or users.partner_id != partners
            or tools.email_normalize(users.login or "") != normalized
            or tools.email_normalize(users.partner_id.email or "") != normalized
        ):
            raise self._trucalc_identity_collision()
        return partners, users

    @api.private
    def _trucalc_assert_plain_portal_reusable(self):
        self.ensure_one()
        user = self.sudo()
        membership = user._trucalc_persona_membership()
        if (
            not user.active or not user.share
            or self.env.ref("base.group_portal") not in user.all_group_ids
            or self.env.ref("base.group_user") in user.all_group_ids
            or self.env.ref("base.group_public") in user.all_group_ids
            or any(membership.values())
            or user.trucalc_bank_company_id or user.trucalc_vendor_id
        ):
            raise self._trucalc_identity_collision()
        return user

    @api.private
    def _trucalc_assert_managed_bank_user(self, bank, require_active=True):
        self.ensure_one()
        bank = bank._trucalc_bank_identity_record(require_active=require_active)
        user = self.sudo().with_context(active_test=False).exists()
        membership = user._trucalc_persona_membership() if user else {}
        if (
            not user or (require_active and not user.active)
            or len(membership.get("bank", self.env["res.groups"])) != 1
            or membership.get("internal") or membership.get("reviewer")
            or membership.get("vendor") or user.trucalc_vendor_id
            or user.trucalc_bank_company_id != bank
            or user.company_id != bank or user.company_ids != bank
            or not user.share
            or self.env.ref("base.group_portal") not in user.all_group_ids
            or self.env.ref("base.group_user") in user.all_group_ids
            or self.env.ref("base.group_public") in user.all_group_ids
        ):
            raise AccessError(_("The Bank user is not configured for this controlled action."))
        if require_active:
            user._trucalc_bank_identity()
        return user

    @api.model
    @api.private
    def _trucalc_provision_bank_user(self, bank, name, login, role, active=True):
        actor = self.env["res.company"]._trucalc_require_bank_administrator()
        bank = bank._trucalc_bank_identity_record()
        normalized = self._trucalc_normalize_login(login)
        clean_name = " ".join((name or "").split())
        role_group = self._trucalc_bank_role_map().get(role)
        if not clean_name or not role_group:
            raise ValidationError(_("Name and one Bank role are required."))
        partner, user = self._trucalc_find_bank_user_identity(normalized)
        if user:
            if user.trucalc_bank_company_id == bank and user._trucalc_bank_role_key():
                if user.active:
                    raise ValidationError(_(
                        "This Bank user already exists. Use the controlled Bank Users actions."
                    ))
                raise ValidationError(_(
                    "This Bank user is inactive. Use the controlled Reactivate action."
                ))
            user._trucalc_assert_plain_portal_reusable()
        elif partner:
            if not partner.active or partner.user_ids:
                raise self._trucalc_identity_collision()
        with self.env.cr.savepoint():
            values = {
                "name": clean_name,
                "login": normalized,
                "email": normalized,
                "active": bool(active),
                "share": True,
                "group_ids": [Command.set([role_group.id])],
                "trucalc_bank_company_id": bank.id,
                "trucalc_vendor_id": False,
                "company_id": bank.id,
                "company_ids": [Command.set([bank.id])],
            }
            if user:
                user.with_context(no_reset_password=True).write(values)
            else:
                if partner:
                    values["partner_id"] = partner.id
                user = self.sudo().with_context(no_reset_password=True).create(values)
            user.invalidate_recordset()
            user._trucalc_assert_managed_bank_user(bank, require_active=bool(active))
            self.env["trucalc.bank.admin.audit"]._trucalc_log(
                "bank_user_created", actor, bank, target_user=user,
                prior_status=False, new_status="active" if active else "inactive",
                metadata={"role": role, "reused_identity": bool(partner)},
            )
        return user

    @api.private
    def _trucalc_change_bank_role(self, bank, role):
        self.ensure_one()
        actor = self.env["res.company"]._trucalc_require_bank_administrator()
        target = self._trucalc_assert_managed_bank_user(bank)
        selected = self._trucalc_bank_role_map().get(role)
        if not selected:
            raise ValidationError(_("Select one Bank role."))
        prior = target._trucalc_bank_role_key()
        if prior == role:
            raise ValidationError(_("The Bank user already has this role."))
        bank_roles = self.env["res.groups"].browse(
            [group.id for group in self._trucalc_bank_role_map().values()]
        )
        direct = target.group_ids - bank_roles
        with self.env.cr.savepoint():
            target.write({
                "group_ids": [Command.set((direct | selected).ids)],
                "trucalc_bank_company_id": bank.id,
                "company_id": bank.id,
                "company_ids": [Command.set([bank.id])],
            })
            target.invalidate_recordset()
            target._trucalc_assert_managed_bank_user(bank)
            self.env["trucalc.bank.admin.audit"]._trucalc_log(
                "role_changed", actor, bank, target_user=target,
                prior_status=prior, new_status=role,
            )
        return True

    @api.private
    def _trucalc_deactivate_bank_user(self, bank):
        self.ensure_one()
        actor = self.env["res.company"]._trucalc_require_bank_administrator()
        target = self._trucalc_assert_managed_bank_user(bank)
        with self.env.cr.savepoint():
            target.write({"active": False})
            target.invalidate_recordset()
            target._trucalc_assert_managed_bank_user(bank, require_active=False)
            if target.active or target.partner_id.signup_type:
                raise ValidationError(_("Bank user deactivation did not complete safely."))
            self.env["trucalc.bank.admin.audit"]._trucalc_log(
                "user_deactivated", actor, bank, target_user=target,
                prior_status="active", new_status="inactive",
            )
        return True

    @api.private
    def _trucalc_reactivate_bank_user(self, bank):
        self.ensure_one()
        actor = self.env["res.company"]._trucalc_require_bank_administrator()
        bank = bank._trucalc_bank_identity_record()
        target = self._trucalc_assert_managed_bank_user(bank, require_active=False)
        if target.active:
            raise ValidationError(_("The Bank user is already active."))
        normalized = self._trucalc_normalize_login(target.login)
        partner, collision = self._trucalc_find_bank_user_identity(normalized)
        if collision != target or partner != target.partner_id:
            raise self._trucalc_identity_collision()
        with self.env.cr.savepoint():
            target.write({
                "active": True, "share": True,
                "trucalc_bank_company_id": bank.id,
                "trucalc_vendor_id": False,
                "company_id": bank.id,
                "company_ids": [Command.set([bank.id])],
            })
            target.invalidate_recordset()
            target._trucalc_assert_managed_bank_user(bank)
            self.env["trucalc.bank.admin.audit"]._trucalc_log(
                "user_reactivated", actor, bank, target_user=target,
                prior_status="inactive", new_status="active",
            )
        return True

    @api.private
    def _trucalc_send_bank_invitation(self, bank):
        self.ensure_one()
        actor = self.env["res.company"]._trucalc_require_bank_administrator()
        target = self._trucalc_assert_managed_bank_user(bank)
        normalized = self._trucalc_normalize_login(target.login)
        partner, collision = self._trucalc_find_bank_user_identity(normalized)
        if collision != target or partner != target.partner_id:
            raise self._trucalc_identity_collision()
        template = self.env.ref("trucalc_orders.mail_template_bank_user_invitation")
        main_company = self.env.ref("base.main_company")
        if not main_company.email:
            raise UserError(_("The TruCalc sender email is not configured."))
        try:
            with self.env.cr.savepoint():
                target.partner_id.sudo().signup_prepare(signup_type="signup")
                mail_id = template.sudo().with_context(
                    dbname=self.env.cr.dbname,
                    lang=target.lang or self.env.lang,
                    allowed_company_ids=[main_company.id],
                ).send_mail(
                    target.id, force_send=True, raise_exception=False,
                    email_values={
                        # Retain the exact rendered outbound artifact for delivery
                        # inspection and for diagnosing a failed SMTP attempt.
                        "auto_delete": False,
                        "email_from": main_company.email_formatted,
                        "email_to": target.email,
                        "recipient_ids": [],
                        "partner_ids": [],
                    },
                )
                if not mail_id:
                    raise UserError(_(
                        "The TruCalc invitation could not be sent. No successful invitation was recorded."
                    ))
                mail = self.env["mail.mail"].sudo().browse(mail_id).exists()
                if not mail:
                    raise UserError(_(
                        "The TruCalc invitation could not be retained for inspection. "
                        "No successful invitation was recorded."
                    ))
                target._trucalc_assert_managed_bank_user(bank)
                sent = mail.state == "sent"
                if sent:
                    self.env["trucalc.bank.admin.audit"]._trucalc_log(
                        "invitation_sent", actor, bank, target_user=target,
                        new_status="pending",
                        metadata={
                            "template": template.get_external_id().get(template.id),
                            "mail_id": mail.id,
                        },
                    )
        except Exception as exc:
            if isinstance(exc, (AccessError, ValidationError, UserError)):
                raise
            raise UserError(_(
                "The TruCalc invitation could not be sent. No successful invitation was recorded."
            )) from exc
        return {
            "sent": sent,
            "mail_id": mail.id,
            "state": mail.state,
        }

    @api.model
    def _get_invalidation_fields(self):
        return super()._get_invalidation_fields() | {
            "trucalc_bank_company_id",
            "trucalc_vendor_id",
        }

    @api.model_create_multi
    def create(self, vals_list):
        users = super().create(vals_list)
        users._trucalc_sync_home_action()
        return users

    def write(self, vals):
        if self.env.context.get("trucalc_home_action_sync"):
            return super().write(vals)
        home_action = self.env.ref("trucalc_orders.action_trucalc_orders")
        previously_restricted = {
            user.id: user._trucalc_is_restricted_internal()
            for user in self
        }
        previous_actions = {user.id: user.action_id for user in self}
        result = super().write(vals)
        self._trucalc_sync_home_action(
            previously_restricted=previously_restricted,
            previous_actions=previous_actions,
            explicit_action="action_id" in vals,
            home_action=home_action,
        )
        return result

    @api.private
    def _trucalc_is_restricted_internal(self):
        self.ensure_one()
        membership = self._trucalc_persona_membership()
        user = self.sudo()
        return bool(
            user.active
            and not user.share
            and membership["reviewer"]
            and not membership["internal"]
            and not membership["bank"]
            and not membership["vendor"]
            and self.env.ref("base.group_user") in user.all_group_ids
            and not user.trucalc_bank_company_id
            and not user.trucalc_vendor_id
        )

    @api.private
    def _trucalc_sync_home_action(
        self, previously_restricted=None, previous_actions=None,
        explicit_action=False, home_action=None,
    ):
        home_action = home_action or self.env.ref(
            "trucalc_orders.action_trucalc_orders"
        )
        previously_restricted = previously_restricted or {}
        previous_actions = previous_actions or {}
        for user in self:
            restricted = user._trucalc_is_restricted_internal()
            if restricted and user.action_id.id != home_action.id:
                user.sudo().with_context(trucalc_home_action_sync=True).write({
                    "action_id": home_action.id,
                })
            elif (
                not restricted
                and previously_restricted.get(user.id)
                and not explicit_action
                and previous_actions.get(user.id).id == home_action.id
                and user.action_id.id == home_action.id
            ):
                user.sudo().with_context(trucalc_home_action_sync=True).write({
                    "action_id": False,
                })

    @api.model
    @api.private
    def _trucalc_persona_groups(self):
        return {
            "internal": self.env["res.groups"].browse([
                self.env.ref("trucalc_orders.group_trucalc_admin").id,
                self.env.ref("trucalc_orders.group_trucalc_operations").id,
            ]),
            "reviewer": self.env.ref("trucalc_orders.group_trucalc_reviewer"),
            "bank": self.env["res.groups"].browse([
                self.env.ref("trucalc_orders.group_bank_admin").id,
                self.env.ref("trucalc_orders.group_bank_requestor").id,
                self.env.ref("trucalc_orders.group_bank_view_only").id,
            ]),
            "vendor": self.env.ref("trucalc_orders.group_vendor_portal"),
        }

    @api.private
    def _trucalc_persona_membership(self):
        self.ensure_one()
        groups = self.sudo().all_group_ids
        persona_groups = self._trucalc_persona_groups()
        return {
            "internal": groups & persona_groups["internal"],
            "reviewer": groups & persona_groups["reviewer"],
            "bank": groups & persona_groups["bank"],
            "vendor": groups & persona_groups["vendor"],
        }

    @api.constrains(
        "group_ids", "trucalc_bank_company_id", "trucalc_vendor_id",
        "company_id", "company_ids",
    )
    def _check_trucalc_persona(self):
        for user in self:
            membership = user._trucalc_persona_membership()
            internal_family = bool(
                membership["internal"] or membership["reviewer"]
            )
            family_count = sum((
                internal_family,
                bool(membership["bank"]),
                bool(membership["vendor"]),
            ))
            user_sudo = user.sudo()
            bank_company = user_sudo.trucalc_bank_company_id
            vendor = user_sudo.trucalc_vendor_id
            effective_groups = user_sudo.all_group_ids
            portal_group = self.env.ref("base.group_portal")
            internal_group = self.env.ref("base.group_user")

            if bank_company and vendor:
                raise ValidationError(_(
                    "A TruCalc user cannot have both bank and vendor mappings."
                ))
            if family_count > 1:
                raise ValidationError(_(
                    "A user may belong to only one TruCalc persona family."
                ))
            if len(membership["internal"]) > 1:
                raise ValidationError(_(
                    "A user may have only one TruCalc Internal role."
                ))
            if membership["reviewer"] and len(membership["internal"]) != 1:
                raise ValidationError(_(
                    "TruCalc Reviewer authorization requires exactly one normal "
                    "TruCalc Internal role."
                ))
            if len(membership["bank"]) > 1:
                raise ValidationError(_(
                    "A user may have only one TruCalc Bank role."
                ))
            if internal_family and (bank_company or vendor):
                raise ValidationError(_(
                    "A TruCalc Internal user cannot have an external authorization mapping."
                ))
            if membership["bank"] and (not bank_company or vendor):
                raise ValidationError(_(
                    "A TruCalc Bank user requires one bank mapping and no vendor mapping."
                ))
            if bank_company and (
                not bank_company.trucalc_is_bank
                or bank_company == self.env.ref("base.main_company")
            ):
                raise ValidationError(_(
                    "A TruCalc Bank user must map to an authorized Bank company."
                ))
            if membership["bank"] and (
                internal_group in effective_groups
                or portal_group not in effective_groups
                or not user_sudo.share
            ):
                raise ValidationError(_(
                    "A TruCalc Bank user must be an external portal user."
                ))
            if membership["bank"] and (
                user_sudo.company_id != bank_company
                or user_sudo.company_ids != bank_company
            ):
                raise ValidationError(_(
                    "A TruCalc Bank user's primary and allowed company must be exactly the mapped Bank."
                ))
            if membership["vendor"] and (not vendor or bank_company):
                raise ValidationError(_(
                    "A TruCalc Vendor user requires one vendor mapping and no bank mapping."
                ))

    @api.private
    def _trucalc_has_bank_role(self):
        self.ensure_one()
        return bool(self._trucalc_persona_membership()["bank"])

    @api.private
    def _trucalc_has_external_role(self):
        self.ensure_one()
        membership = self._trucalc_persona_membership()
        return bool(membership["bank"] or membership["vendor"])

    @api.private
    def _trucalc_bank_identity(self):
        self.ensure_one()
        membership = self._trucalc_persona_membership()
        user = self.sudo()
        effective_groups = user.all_group_ids
        if (
            not user.active
            or len(membership["bank"]) != 1
            or membership["internal"]
            or membership["vendor"]
            or not user.trucalc_bank_company_id
            or not user.trucalc_bank_company_id.trucalc_is_bank
            or not user.trucalc_bank_company_id.trucalc_bank_active
            or user.trucalc_bank_company_id == self.env.ref("base.main_company")
            or user.trucalc_vendor_id
            or self.env.ref("base.group_user") in effective_groups
            or self.env.ref("base.group_portal") not in effective_groups
            or not user.share
            or user.company_id != user.trucalc_bank_company_id
            or user.company_ids != user.trucalc_bank_company_id
        ):
            raise AccessError(_("TruCalc bank authorization is not configured."))
        return user.trucalc_bank_company_id

    @api.private
    def _trucalc_vendor_identity(self):
        self.ensure_one()
        membership = self._trucalc_persona_membership()
        user = self.sudo()
        effective_groups = user.all_group_ids
        vendor = user.trucalc_vendor_id
        if (
            not user.active
            or len(membership["vendor"]) != 1
            or membership["internal"]
            or membership["reviewer"]
            or membership["bank"]
            or not vendor
            or not vendor.active
            or user.trucalc_bank_company_id
            or self.env.ref("base.group_user") in effective_groups
            or self.env.ref("base.group_portal") not in effective_groups
            or not user.share
        ):
            raise AccessError(_("TruCalc vendor authorization is not configured."))
        return vendor

    @api.private
    def _trucalc_reviewer_identity(self):
        self.ensure_one()
        membership = self._trucalc_persona_membership()
        user = self.sudo()
        if (
            not user.active
            or user.share
            or membership["bank"]
            or membership["vendor"]
            or len(membership["internal"]) != 1
            or not membership["reviewer"]
            or self.env.ref("base.group_user") not in user.all_group_ids
            or user.trucalc_bank_company_id
            or user.trucalc_vendor_id
        ):
            raise ValidationError(_(
                "The assigned Reviewer user must be an active internal TruCalc "
                "Reviewer."
            ))
        return user

    @api.private
    def _trucalc_provision_vendor_portal(self, vendor, actor):
        self.ensure_one()
        vendor.ensure_one()
        actor.ensure_one()

        if actor != self.env.user or not actor.has_group(
            "trucalc_orders.group_trucalc_admin"
        ):
            raise AccessError(_("Only TruCalc Administrators may provision vendor users."))

        target = self.sudo().exists()
        selected_vendor = vendor.sudo().exists()
        if not target or not selected_vendor or not selected_vendor.active:
            raise ValidationError(_("The portal user and an active vendor are required."))

        groups = target.all_group_ids
        persona = target._trucalc_persona_membership()
        portal_group = self.env.ref("base.group_portal")
        internal_group = self.env.ref("base.group_user")
        vendor_group = self.env.ref("trucalc_orders.group_vendor_portal")
        if (
            not target.active
            or not target.share
            or portal_group not in groups
            or internal_group in groups
            or persona["internal"]
            or persona["bank"]
            or persona["vendor"]
            or target.trucalc_bank_company_id
            or target.trucalc_vendor_id
            or vendor_group in groups
        ):
            raise ValidationError(_("The selected user is not eligible for vendor provisioning."))

        target.write({
            "trucalc_vendor_id": selected_vendor.id,
            "group_ids": [Command.link(vendor_group.id)],
        })
        target.invalidate_recordset(["group_ids", "trucalc_vendor_id"])
        if (
            target.trucalc_vendor_id != selected_vendor
            or not target.has_group("trucalc_orders.group_vendor_portal")
            or target.has_group("base.group_user")
        ):
            raise ValidationError(_("Vendor persona provisioning did not complete safely."))

        _logger.info(
            "TruCalc vendor portal provisioning succeeded: db=%s actor_id=%s "
            "actor_login=%s target_user_id=%s target_login=%s vendor_id=%s vendor_name=%s",
            self.env.cr.dbname,
            actor.id,
            actor.login,
            target.id,
            target.login,
            selected_vendor.id,
            selected_vendor.name,
        )
        return True
