from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


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

    @api.model
    @api.private
    def _trucalc_persona_groups(self):
        return {
            "internal": self.env["res.groups"].browse([
                self.env.ref("trucalc_orders.group_trucalc_admin").id,
                self.env.ref("trucalc_orders.group_trucalc_operations").id,
                self.env.ref("trucalc_orders.group_trucalc_reviewer").id,
            ]),
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
            "bank": groups & persona_groups["bank"],
            "vendor": groups & persona_groups["vendor"],
        }

    @api.constrains("group_ids", "trucalc_bank_company_id", "trucalc_vendor_id")
    def _check_trucalc_persona(self):
        for user in self:
            membership = user._trucalc_persona_membership()
            family_count = sum(bool(groups) for groups in membership.values())
            bank_company = user.sudo().trucalc_bank_company_id
            vendor = user.sudo().trucalc_vendor_id

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
            if len(membership["bank"]) > 1:
                raise ValidationError(_(
                    "A user may have only one TruCalc Bank role."
                ))
            if membership["internal"] and (bank_company or vendor):
                raise ValidationError(_(
                    "A TruCalc Internal user cannot have an external authorization mapping."
                ))
            if membership["bank"] and (not bank_company or vendor):
                raise ValidationError(_(
                    "A TruCalc Bank user requires one bank mapping and no vendor mapping."
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
        if (
            len(membership["bank"]) != 1
            or membership["internal"]
            or membership["vendor"]
            or not user.trucalc_bank_company_id
            or user.trucalc_vendor_id
        ):
            raise AccessError(_("TruCalc bank authorization is not configured."))
        return user.trucalc_bank_company_id
