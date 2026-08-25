from odoo import api, fields, models, tools, _
from odoo.exceptions import AccessError


class TruCalcVendorOrder(models.Model):
    _name = "trucalc.vendor.order"
    _description = "Vendor Order"
    _auto = False
    _rec_name = "order_number"
    _order = "order_number, id"

    order_number = fields.Char(readonly=True)
    borrower = fields.Char(readonly=True)
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
        [("invitation", "Open for Bid"), ("assignment", "Assigned"),
         ("declined_history", "Declined")],
        readonly=True,
    )
    vendor_status = fields.Selection(
        [
            ("open_for_bid", "Open for Bid"),
            ("assigned", "Assigned"),
            ("report_received", "Report Received"),
            ("reviewer_assigned", "Reviewer Assigned"),
            ("under_review", "Under Review"),
            ("declined", "Declined"),
        ],
        readonly=True,
    )
    order_status = fields.Selection(
        selection=lambda self: self.env["trucalc.order"]._fields["status"].selection,
        readonly=True,
    )
    response_deadline = fields.Datetime(readonly=True)
    due_date = fields.Date(readonly=True)
    is_assigned = fields.Boolean(readonly=True)
    agreed_vendor_fee = fields.Float(readonly=True)
    solicitation_standard_fee = fields.Float(
        string="Standard Fee", readonly=True,
        help="Standard fee snapshotted for this Vendor's solicitation.",
    )
    requested_delivery_date = fields.Date(readonly=True)
    invitation_state = fields.Selection(
        [("invited", "Invited"), ("declined", "Declined"),
         ("revoked", "Revoked"), ("expired", "Expired"), ("closed", "Closed")],
        readonly=True,
    )
    vendor_decline_reason = fields.Text(readonly=True)
    response_type = fields.Selection(
        selection=lambda self: self.env["trucalc.bid"]._fields["response_type"].selection,
        readonly=True,
    )
    proposed_fee = fields.Float(readonly=True)
    proposed_delivery_date = fields.Date(readonly=True)
    vendor_comments = fields.Text(readonly=True)
    response_status = fields.Selection(
        selection=lambda self: self.env["trucalc.bid"]._fields["status"].selection,
        readonly=True,
    )
    submitted_at = fields.Datetime(readonly=True)
    last_revised_at = fields.Datetime(readonly=True)
    revision_count = fields.Integer(readonly=True)
    can_respond = fields.Boolean(readonly=True)
    vendor_response_label = fields.Char(string="Your Response", readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)

    # Integer-only and system-restricted: required for the rule, but deliberately
    # provides no relational path from the public projection to Vendor records.
    vendor_id = fields.Integer(readonly=True, groups="base.group_system")

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(f"""
            CREATE VIEW {self._table} AS (
                SELECT DISTINCT ON (a.vendor_id, a.order_id)
                    a.id, a.vendor_id,
                    o.order_number, o.borrower, o.service_type, o.property_type,
                    o.property_address, o.city, o.state, o.zip_code, o.bidding_round,
                    CASE WHEN a.active IS NOT TRUE THEN 'declined_history'
                         ELSE a.source END AS vendor_phase,
                    CASE WHEN a.active IS NOT TRUE THEN 'declined'
                         WHEN a.source = 'invitation' THEN 'open_for_bid'
                         WHEN o.status IN ('assigned', 'report_received',
                              'reviewer_assigned', 'under_review') THEN o.status
                         ELSE NULL END AS vendor_status,
                    o.status AS order_status,
                    CASE WHEN a.source = 'invitation' THEN a.expires_at END
                        AS response_deadline,
                    o.due_date,
                    (a.source = 'assignment') AS is_assigned,
                    CASE WHEN a.source = 'assignment' THEN o.vendor_fee END
                        AS agreed_vendor_fee,
                    CASE WHEN a.source = 'invitation' THEN i.standard_fee END
                        AS solicitation_standard_fee,
                    i.requested_delivery_date,
                    i.state AS invitation_state,
                    i.decline_reason AS vendor_decline_reason,
                    b.response_type,
                    b.bid_amount AS proposed_fee,
                    b.proposed_delivery_date,
                    b.notes AS vendor_comments,
                    b.status AS response_status,
                    b.submitted_at, b.last_revised_at, b.revision_count,
                    CASE
                        WHEN a.active IS NOT TRUE THEN 'Declined'
                        WHEN b.status = 'submitted' THEN 'Submitted'
                        WHEN b.status = 'selected' THEN 'Selected'
                        WHEN b.status = 'not_selected' THEN 'Not Selected'
                        WHEN b.status = 'disqualified' THEN 'Disqualified'
                        WHEN i.state = 'expired' THEN 'Expired'
                        WHEN a.source = 'invitation' AND i.state = 'invited'
                            THEN 'Open for Response'
                        ELSE NULL
                    END AS vendor_response_label,
                    company.currency_id AS currency_id,
                    (a.active IS TRUE AND a.source = 'invitation'
                     AND i.state = 'invited' AND o.status = 'bid_requested'
                     AND i.requested_delivery_date IS NOT NULL
                     AND i.standard_fee IS NOT NULL
                     AND i.round_number = o.bidding_round
                     AND (i.response_deadline IS NULL
                          OR CURRENT_TIMESTAMP AT TIME ZONE 'UTC' <= i.response_deadline)
                     AND (b.id IS NULL OR b.status = 'submitted')) AS can_respond
                FROM trucalc_order_vendor_authorization a
                JOIN trucalc_order o ON o.id = a.order_id
                JOIN trucalc_vendor v ON v.id = a.vendor_id
                JOIN res_company company ON company.id = o.company_id
                LEFT JOIN trucalc_bid b
                    ON b.response_type IS NOT NULL
                   AND (
                        (a.source = 'invitation' AND b.invitation_id = a.invitation_id)
                        OR (
                            a.source = 'assignment'
                            AND b.order_id = a.order_id
                            AND b.vendor_id = a.vendor_id
                            AND b.round_number = a.round_number
                            AND b.status = 'selected'
                        )
                   )
                LEFT JOIN trucalc_bid_invitation i
                    ON i.id = COALESCE(a.invitation_id, b.invitation_id)
                WHERE (
                    a.active IS TRUE
                    OR (a.active IS NOT TRUE
                        AND a.deauthorization_reason = 'declined'
                        AND i.state = 'declined'
                        AND a.vendor_id = i.vendor_id
                        AND a.order_id = i.order_id
                        AND a.round_number = i.round_number)
                )
                  AND v.active IS TRUE
                  AND a.source IN ('invitation', 'assignment')
                  AND (a.source = 'invitation'
                       OR o.status IN ('assigned', 'report_received',
                           'reviewer_assigned', 'under_review'))
                ORDER BY a.vendor_id, a.order_id,
                    CASE WHEN a.active IS TRUE AND a.source = 'assignment' THEN 0
                         WHEN a.active IS TRUE THEN 1 ELSE 2 END,
                    a.authorized_at DESC, a.id DESC
            )
        """)

    def _trusted_invitation(self):
        self.ensure_one()
        vendor = self.env["trucalc.bid.invitation"]._vendor_identity()
        authorization = self.env["trucalc.order.vendor.authorization"].sudo().browse(
            self.id
        ).exists()
        invitation = authorization.invitation_id
        if (
            not authorization or not authorization.active
            or authorization.vendor_id != vendor
            or authorization.source != "invitation" or not invitation
            or invitation.vendor_id != vendor
            or invitation.order_id != authorization.order_id
            or invitation.round_number != authorization.round_number
        ):
            raise AccessError(_("Vendor response access is not authorized."))
        return invitation

    def action_vendor_response(self, response_type, proposed_fee=None,
                               proposed_delivery_date=None, comments=None):
        invitation = self._trusted_invitation()
        return invitation.with_user(self.env.user).action_vendor_submit_response(
            response_type, proposed_fee=proposed_fee,
            proposed_delivery_date=proposed_delivery_date, comments=comments,
        )

    def action_vendor_decline(self, reason):
        invitation = self._trusted_invitation()
        return invitation.with_user(self.env.user).action_vendor_decline(reason)

    @api.model_create_multi
    def create(self, vals_list):
        raise AccessError(_("Vendor Orders are read-only."))

    def write(self, vals):
        raise AccessError(_("Vendor Orders are read-only."))

    def unlink(self):
        raise AccessError(_("Vendor Orders are read-only."))
