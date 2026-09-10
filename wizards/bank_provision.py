from odoo import fields, models, _
from odoo.exceptions import AccessError


class TruCalcBankProvision(models.TransientModel):
    _name = "trucalc.bank.provision"
    _description = "Create or Edit TruCalc Bank"

    bank_company_id = fields.Many2one(
        "res.company",
        string="Bank",
        readonly=True,
        domain=[("trucalc_is_bank", "=", True)],
    )
    name = fields.Char(string="Bank Name", required=True)
    currency_id = fields.Many2one(
        "res.currency",
        required=True,
        default=lambda self: self.env.ref("base.main_company").currency_id,
        domain=[("active", "=", True)],
    )
    bank_active = fields.Boolean(string="Bank Active", default=True, readonly=True)
    street = fields.Char()
    street2 = fields.Char()
    city = fields.Char()
    state_id = fields.Many2one("res.country.state", string="State")
    zip = fields.Char(string="ZIP")
    country_id = fields.Many2one("res.country", string="Country")
    phone = fields.Char()
    email = fields.Char()
    website = fields.Char()
    logo = fields.Binary()

    def _profile_values(self):
        field_names = (
            "name", "currency_id", "street", "street2", "city",
            "state_id", "zip", "country_id", "phone", "email",
            "website", "logo",
        )
        return {
            field_name: self._fields[field_name].convert_to_write(
                self[field_name], self
            )
            for field_name in field_names
        }

    def _check_actor(self):
        self.env["res.company"]._trucalc_require_bank_administrator()

    def action_save(self):
        self.ensure_one()
        self._check_actor()
        values = self._profile_values()
        if self.bank_company_id:
            bank = self.bank_company_id._trucalc_update_bank_profile(values)
        else:
            bank = self.env["res.company"]._trucalc_create_bank(values)
        return {
            "type": "ir.actions.act_window",
            "name": _("Bank"),
            "res_model": "res.company",
            "res_id": bank.id,
            "view_mode": "form",
            "views": [(self.env.ref("trucalc_orders.view_trucalc_bank_form").id, "form")],
            "target": "current",
        }

    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        bank_id = values.get("bank_company_id") or self.env.context.get("default_bank_company_id")
        if not bank_id:
            return values
        bank = self.env["res.company"].browse(bank_id)._trucalc_bank_identity_record(
            require_active=False
        )
        self._check_actor()
        for field_name in (
            "name", "currency_id", "street", "street2", "city", "state_id",
            "zip", "country_id", "phone", "email", "website", "logo",
        ):
            value = bank[field_name]
            values[field_name] = value.id if value and hasattr(value, "id") else value
        values["bank_active"] = bank.trucalc_bank_active
        return values

    def unlink(self):
        # Transient cancellation is harmless, but only the authorized surface may use it.
        if self and not self.env.is_superuser():
            self._check_actor()
        return super().unlink()
