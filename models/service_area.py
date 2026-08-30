import re

from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError

from .vendor_fee import SERVICE_SELECTION


class TruCalcServiceArea(models.Model):
    _name = "trucalc.service.area"
    _description = "TruCalc Service Area"
    _order = "state_id, county_normalized, service_type, id"

    state_id = fields.Many2one(
        "res.country.state", string="State", required=True, ondelete="restrict",
    )
    county = fields.Char(required=True)
    county_normalized = fields.Char(required=True, readonly=True, index=True)
    service_type = fields.Selection(SERVICE_SELECTION, required=True)
    active = fields.Boolean(default=True, required=True, index=True)

    _state_county_service_unique = models.Constraint(
        "UNIQUE(state_id, county_normalized, service_type)",
        "A State, County, and Service Type combination may be configured only once.",
    )

    @api.model
    def _normalize_county(self, value):
        county = re.sub(r"\s+", " ", value.strip()) if isinstance(value, str) else ""
        if not county:
            raise ValidationError(_("County is required."))
        if len(county) > 128:
            raise ValidationError(_("County may not exceed 128 characters."))
        return county, county.casefold()

    def _require_admin(self):
        if not self.env.su and not self.env.user.has_group(
            "trucalc_orders.group_trucalc_admin"
        ):
            raise AccessError(_("Only TruCalc Administrators may maintain Service Areas."))

    @api.model
    def _check_duplicate(self, state_id, county_normalized, service_type, exclude=None):
        domain = [
            ("state_id", "=", state_id),
            ("county_normalized", "=", county_normalized),
            ("service_type", "=", service_type),
        ]
        if exclude:
            domain.append(("id", "not in", exclude.ids))
        if self.sudo().with_context(active_test=False).search_count(domain):
            raise ValidationError(_(
                "A State, County, and Service Type combination may be configured only once."
            ))

    @api.model_create_multi
    def create(self, vals_list):
        self._require_admin()
        prepared = []
        for incoming in vals_list:
            vals = dict(incoming)
            county, normalized = self._normalize_county(vals.get("county"))
            vals.update(county=county, county_normalized=normalized)
            self._check_duplicate(
                vals.get("state_id"), normalized, vals.get("service_type")
            )
            prepared.append(vals)
        return super().create(prepared)

    def write(self, vals):
        self._require_admin()
        vals = dict(vals)
        if "county_normalized" in vals and "county" not in vals:
            raise AccessError(_("Normalized County is system-controlled."))
        if "county" in vals:
            county, normalized = self._normalize_county(vals["county"])
            vals.update(county=county, county_normalized=normalized)
        for area in self:
            self._check_duplicate(
                vals.get("state_id", area.state_id.id),
                vals.get("county_normalized", area.county_normalized),
                vals.get("service_type", area.service_type),
                exclude=area,
            )
        return super().write(vals)

    def unlink(self):
        raise AccessError(_("Service Areas must be archived instead of deleted."))
