from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


class TruCalcNegotiatedFee(models.Model):
    _name = "trucalc.negotiated.fee"
    _description = "Negotiated Bank Fee Schedule"
    _order = "bank_id, service_area_id, id"

    bank_id = fields.Many2one(
        "res.company", string="Bank", required=True, index=True,
        ondelete="restrict",
        domain=[
            ("trucalc_is_bank", "=", True),
            ("trucalc_bank_active", "=", True),
        ],
    )
    service_area_id = fields.Many2one(
        "trucalc.service.area", string="Service Area", required=True,
        index=True, ondelete="restrict",
    )
    negotiated_fee = fields.Monetary(
        string="Negotiated Fee", required=True, currency_field="currency_id",
    )
    currency_id = fields.Many2one(
        related="service_area_id.currency_id", readonly=True, store=True,
    )
    active = fields.Boolean(default=True, required=True, index=True)

    _bank_service_area_unique = models.Constraint(
        "UNIQUE(bank_id, service_area_id)",
        "A Bank may have only one negotiated fee for a Service Area.",
    )

    @api.constrains("negotiated_fee")
    def _check_negotiated_fee(self):
        for schedule in self:
            if schedule.negotiated_fee < 0:
                raise ValidationError(_("Negotiated Fee cannot be negative."))

    def _require_admin(self):
        if not self.env.su and not self.env.user.has_group(
            "trucalc_orders.group_trucalc_admin"
        ):
            raise AccessError(_(
                "Only TruCalc Administrators may maintain Negotiated Fees."
            ))

    @api.model
    def _check_duplicate(self, bank_id, service_area_id, exclude=None):
        domain = [
            ("bank_id", "=", bank_id),
            ("service_area_id", "=", service_area_id),
        ]
        if exclude:
            domain.append(("id", "not in", exclude.ids))
        if self.sudo().with_context(active_test=False).search_count(domain):
            raise ValidationError(_(
                "A Bank may have only one negotiated fee for a Service Area."
            ))

    @api.model_create_multi
    def create(self, vals_list):
        self._require_admin()
        keys = sorted({
            (vals.get("bank_id"), vals.get("service_area_id"))
            for vals in vals_list
        })
        for bank_id, service_area_id in keys:
            self.env["trucalc.order"]._lock_pricing_key(
                bank_id, service_area_id,
            )
        for vals in vals_list:
            self._check_duplicate(
                vals.get("bank_id"), vals.get("service_area_id"),
            )
        return super().create(vals_list)

    def write(self, vals):
        self._require_admin()
        keys = {
            (schedule.bank_id.id, schedule.service_area_id.id)
            for schedule in self
        }
        keys.update({
            (
                vals.get("bank_id", schedule.bank_id.id),
                vals.get("service_area_id", schedule.service_area_id.id),
            )
            for schedule in self
        })
        for bank_id, service_area_id in sorted(keys):
            self.env["trucalc.order"]._lock_pricing_key(
                bank_id, service_area_id,
            )
        for schedule in self:
            self._check_duplicate(
                vals.get("bank_id", schedule.bank_id.id),
                vals.get("service_area_id", schedule.service_area_id.id),
                exclude=schedule,
            )
        return super().write(vals)

    def unlink(self):
        raise AccessError(_("Negotiated Fees must be archived instead of deleted."))
