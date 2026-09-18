from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

from ..models.vendor_fee import SERVICE_SELECTION


class LocationCorrectionWizard(models.TransientModel):
    _name = "trucalc.location.correction.wizard"
    _description = "Correct Service Area / Property Location"

    order_id = fields.Many2one("trucalc.order", required=True, readonly=True)
    current_state_id = fields.Many2one(
        "res.country.state", required=True, readonly=True, string="Current State",
    )
    current_county = fields.Char(
        required=True, readonly=True, string="Current County",
    )
    current_service_area_id = fields.Many2one(
        "trucalc.service.area", required=True, readonly=True,
        string="Current Service Area",
    )
    service_type = fields.Selection(
        SERVICE_SELECTION, required=True, readonly=True, string="Service Type",
    )
    currency_id = fields.Many2one(
        "res.currency", required=True, readonly=True,
    )
    current_effective_fee = fields.Monetary(
        required=True, readonly=True, string="Current Effective Fee",
    )
    corrected_state_id = fields.Many2one(
        "res.country.state", required=True, string="Corrected State",
    )
    corrected_county_area_id = fields.Many2one(
        "trucalc.service.area", required=True, string="Corrected County",
    )
    corrected_service_area_id = fields.Many2one(
        "trucalc.service.area", readonly=True, string="Corrected Service Area",
    )
    corrected_schedule_fee = fields.Monetary(
        readonly=True, string="Corrected Schedule Fee",
    )
    fee_difference = fields.Monetary(readonly=True)
    fee_result = fields.Selection([
        ("same", "Same Fee"),
        ("higher", "Higher Fee — Bank Approval Required"),
        ("lower", "Lower Fee — Not Supported"),
    ], readonly=True, string="Fee Result")
    correction_reason = fields.Text(required=True, string="Correction Reason")
    available_state_ids = fields.Many2many(
        "res.country.state", compute="_compute_available_locations",
    )
    available_county_area_ids = fields.Many2many(
        "trucalc.service.area", compute="_compute_available_locations",
    )

    @api.depends("order_id.service_type", "corrected_state_id")
    def _compute_available_locations(self):
        Area = self.env["trucalc.service.area"].sudo()
        for wizard in self:
            areas = Area.search([
                ("active", "=", True),
                ("service_type", "=", wizard.order_id.service_type),
            ]) if wizard.order_id.service_type else Area.browse()
            wizard.available_state_ids = areas.mapped("state_id")
            state_areas = areas.filtered(
                lambda area: area.state_id == wizard.corrected_state_id
            )
            representatives = Area.browse()
            seen = set()
            for area in state_areas.sorted(
                key=lambda item: (item.county_normalized, item.id)
            ):
                if area.county_normalized not in seen:
                    representatives |= area
                    seen.add(area.county_normalized)
            wizard.available_county_area_ids = representatives

    @api.model
    def default_get(self, field_names):
        values = super().default_get(field_names)
        order = self.env["trucalc.order"].browse(values.get("order_id")).exists()
        if len(order) == 1:
            order._require_location_correction_actor()
            order._validate_location_correction_eligibility()
            values.update({
                "current_state_id": order.service_area_id.state_id.id,
                "current_county": order.county,
                "current_service_area_id": order.service_area_id.id,
                "service_type": order.service_type,
                "currency_id": order.fee_currency_id.id,
                "current_effective_fee": order.current_agreed_fee,
                "corrected_state_id": order.service_area_id.state_id.id,
                "corrected_county_area_id": order.service_area_id.id,
            })
        return values

    @api.onchange("corrected_state_id")
    def _onchange_corrected_state(self):
        for wizard in self:
            if (
                wizard.corrected_county_area_id
                and wizard.corrected_county_area_id.state_id
                != wizard.corrected_state_id
            ):
                wizard.corrected_county_area_id = False
            wizard._preview_correction()

    @api.onchange("corrected_county_area_id")
    def _onchange_corrected_county(self):
        for wizard in self:
            wizard._preview_correction()

    def _preview_correction(self):
        self.ensure_one()
        self.corrected_service_area_id = False
        self.corrected_schedule_fee = False
        self.fee_difference = False
        self.fee_result = False
        county_area = self.corrected_county_area_id
        if not self.order_id or not self.corrected_state_id or not county_area:
            return
        try:
            resolution = self.order_id._resolve_location_correction_pricing(
                self.corrected_state_id, county_area.county,
            )
        except ValidationError:
            return
        self.corrected_service_area_id = resolution["area"]
        self.corrected_schedule_fee = resolution["pricing"]["agreed_fee"]
        self.fee_difference = resolution["difference"]
        self.fee_result = resolution["direction"]

    def action_confirm(self):
        self.ensure_one()
        county_area = self.corrected_county_area_id
        if not county_area:
            raise ValidationError(_("Select a corrected County."))
        resolution = self.order_id._resolve_location_correction_pricing(
            self.corrected_state_id, county_area.county,
        )
        if resolution["direction"] == "same":
            self.order_id._apply_same_fee_location_correction(
                self.corrected_state_id,
                county_area.county,
                self.correction_reason,
                self.current_service_area_id.id,
            )
        elif resolution["direction"] == "higher":
            self.order_id._stage_higher_fee_location_correction(
                self.corrected_state_id,
                county_area.county,
                self.correction_reason,
                self.current_service_area_id.id,
            )
        else:
            raise ValidationError(_(
                "Lower-fee property location correction is not supported."
            ))
        return {"type": "ir.actions.act_window_close"}
