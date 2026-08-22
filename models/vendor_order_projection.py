from odoo import api, fields, models, tools, _
from odoo.exceptions import AccessError


class TruCalcVendorOrder(models.Model):
    _name = "trucalc.vendor.order"
    _description = "Vendor Order"
    _auto = False
    _rec_name = "order_number"
    _order = "order_number, id"

    order_number = fields.Char(readonly=True)
    service_type = fields.Selection(
        selection=lambda self: self.env["trucalc.order"]._fields[
            "service_type"
        ].selection,
        readonly=True,
    )
    property_type = fields.Selection(
        selection=lambda self: self.env["trucalc.order"]._fields[
            "property_type"
        ].selection,
        readonly=True,
    )
    property_address = fields.Char(readonly=True)
    city = fields.Char(readonly=True)
    state = fields.Char(readonly=True)
    zip_code = fields.Char(string="ZIP", readonly=True)
    bidding_round = fields.Integer(readonly=True)
    vendor_phase = fields.Selection(
        [("invitation", "Open for Bid"), ("assignment", "Assigned")],
        readonly=True,
    )
    vendor_status = fields.Selection(
        [
            ("open_for_bid", "Open for Bid"),
            ("assigned", "Assigned"),
            ("report_received", "Report Received"),
            ("reviewer_assigned", "Reviewer Assigned"),
            ("under_review", "Under Review"),
        ],
        readonly=True,
    )
    response_deadline = fields.Datetime(readonly=True)
    due_date = fields.Date(readonly=True)
    is_assigned = fields.Boolean(readonly=True)
    agreed_vendor_fee = fields.Float(readonly=True)

    # Integer-only and system-restricted: required for the rule, but deliberately
    # provides no relational path from the public projection to Vendor records.
    vendor_id = fields.Integer(readonly=True, groups="base.group_system")

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(f"""
            CREATE VIEW {self._table} AS (
                SELECT DISTINCT ON (authorization_record.vendor_id, authorization_record.order_id)
                    authorization_record.id AS id,
                    authorization_record.vendor_id AS vendor_id,
                    order_record.order_number AS order_number,
                    order_record.service_type AS service_type,
                    order_record.property_type AS property_type,
                    order_record.property_address AS property_address,
                    order_record.city AS city,
                    order_record.state AS state,
                    order_record.zip_code AS zip_code,
                    order_record.bidding_round AS bidding_round,
                    authorization_record.source AS vendor_phase,
                    CASE
                        WHEN authorization_record.source = 'invitation' THEN 'open_for_bid'
                        WHEN order_record.status IN (
                            'assigned', 'report_received', 'reviewer_assigned', 'under_review'
                        ) THEN order_record.status
                        ELSE NULL
                    END AS vendor_status,
                    CASE WHEN authorization_record.source = 'invitation'
                        THEN authorization_record.expires_at ELSE NULL END AS response_deadline,
                    CASE WHEN authorization_record.source = 'assignment'
                        THEN order_record.due_date ELSE NULL END AS due_date,
                    (authorization_record.source = 'assignment') AS is_assigned,
                    CASE WHEN authorization_record.source = 'assignment'
                        THEN order_record.vendor_fee ELSE NULL END AS agreed_vendor_fee
                FROM trucalc_order_vendor_authorization authorization_record
                JOIN trucalc_order order_record ON order_record.id = authorization_record.order_id
                JOIN trucalc_vendor vendor ON vendor.id = authorization_record.vendor_id
                WHERE authorization_record.active IS TRUE
                  AND vendor.active IS TRUE
                  AND authorization_record.source IN ('invitation', 'assignment')
                  AND (
                    authorization_record.source = 'invitation'
                    OR order_record.status IN (
                        'assigned', 'report_received', 'reviewer_assigned', 'under_review'
                    )
                  )
                ORDER BY
                    authorization_record.vendor_id,
                    authorization_record.order_id,
                    CASE authorization_record.source WHEN 'assignment' THEN 0 ELSE 1 END,
                    authorization_record.authorized_at DESC,
                    authorization_record.id DESC
            )
        """)

    @api.model_create_multi
    def create(self, vals_list):
        raise AccessError(_("Vendor Orders are read-only."))

    def write(self, vals):
        raise AccessError(_("Vendor Orders are read-only."))

    def unlink(self):
        raise AccessError(_("Vendor Orders are read-only."))
