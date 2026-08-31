import logging

from odoo import Command, api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


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

    @api.constrains("group_ids", "trucalc_bank_company_id", "trucalc_vendor_id")
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
            if membership["bank"] and (
                internal_group in effective_groups
                or portal_group not in effective_groups
                or not user_sudo.share
            ):
                raise ValidationError(_(
                    "A TruCalc Bank user must be an external portal user."
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
            len(membership["bank"]) != 1
            or membership["internal"]
            or membership["vendor"]
            or not user.trucalc_bank_company_id
            or user.trucalc_vendor_id
            or self.env.ref("base.group_user") in effective_groups
            or self.env.ref("base.group_portal") not in effective_groups
            or not user.share
        ):
            raise AccessError(_("TruCalc bank authorization is not configured."))
        return user.trucalc_bank_company_id

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
