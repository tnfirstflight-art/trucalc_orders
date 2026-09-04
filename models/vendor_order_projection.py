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
    inspection_contact_name = fields.Char(
        string="Inspection Contact Person", readonly=True,
    )
    inspection_contact_phone = fields.Char(
        string="Inspection Contact Phone", readonly=True,
    )
    inspection_contact_phone_display = fields.Char(
        string="Inspection Contact Phone",
        compute="_compute_inspection_contact_phone_display",
        readonly=True,
    )
    inspection_contact_email = fields.Char(
        string="Inspection Contact Email", readonly=True,
    )
    bidding_round = fields.Integer(readonly=True)
    vendor_phase = fields.Selection(
        [("invitation", "Open for Bid"), ("assignment", "Engaged"),
         ("declined_history", "Declined")],
        readonly=True,
    )
    vendor_status = fields.Selection(
        [
            ("open_for_bid", "Open for Bid"),
            ("assigned", "Assigned"),
            ("engaged", "Engaged"),
            ("report_received", "Report Received"),
            ("reviewer_assigned", "Reviewer Assigned"),
            ("under_review", "Under Review"),
            ("completed", "Completed"),
            ("declined", "Declined"),
        ],
        readonly=True,
    )
    order_status = fields.Selection(
        selection=lambda self: self.env["trucalc.order"]._fields["status"].selection,
        readonly=True,
    )
    response_deadline = fields.Datetime(readonly=True)
    vendor_delivery_date = fields.Date(readonly=True)
    vendor_engaged_at = fields.Datetime(readonly=True)
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
    vendor_response_label = fields.Char(string="Response", readonly=True)
    engagement_response_state = fields.Selection(
        selection=lambda self: self.env["trucalc.vendor.engagement"]._fields[
            "response_state"
        ].selection,
        readonly=True,
    )
    engagement_response_label = fields.Char(
        string="Engagement Response", readonly=True,
    )
    pending_requested_delivery_date = fields.Date(readonly=True)
    pending_request_reason = fields.Text(readonly=True)
    engagement_decline_reason = fields.Text(readonly=True)
    can_accept_engagement = fields.Boolean(readonly=True)
    can_request_delivery_change = fields.Boolean(readonly=True)
    can_decline_engagement = fields.Boolean(readonly=True)
    currency_id = fields.Many2one("res.currency", readonly=True)
    review_indicator = fields.Selection(
        [("revision_request", "Revision Request"), ("submitted", "Submitted")],
        compute="_compute_review_indicator", compute_sudo=True, readonly=True,
    )

    # Integer-only and system-restricted: required for the rule, but deliberately
    # provides no relational path from the public projection to Vendor records.
    vendor_id = fields.Integer(readonly=True, groups="base.group_system")

    @api.depends("inspection_contact_phone")
    def _compute_inspection_contact_phone_display(self):
        formatter = self.env["trucalc.order"]._format_inspection_contact_phone
        for projection in self:
            projection.inspection_contact_phone_display = formatter(
                projection.inspection_contact_phone
            )

    def _compute_review_indicator(self):
        indicators = {}
        authorizations = self.env[
            "trucalc.order.vendor.authorization"
        ].sudo().browse(self.ids).exists()
        orders = authorizations.mapped("order_id").filtered(
            lambda order: order.status == "under_review"
        )
        current_by_order = {}
        if orders:
            for valuation in self.env["trucalc.vendor.deliverable"].sudo().search([
                ("order_id", "in", orders.ids),
                ("artifact_type", "=", "valuation"),
                ("is_current", "=", True),
            ]):
                current_by_order[valuation.order_id.id] = valuation
        Event = self.env["trucalc.order.lifecycle.event"].sudo()
        current_ids = [valuation.id for valuation in current_by_order.values()]
        open_target_ids = set()
        submitted_new_ids = set()
        if current_ids:
            requests = Event.search([
                ("event_type", "=", "valuation_revision_requested"),
                ("target_valuation_id", "in", current_ids),
            ])
            consumed_request_ids = set(Event.search([
                ("event_type", "=", "valuation_revision_submitted"),
                ("revision_request_event_id", "in", requests.ids),
            ]).mapped("revision_request_event_id").ids)
            open_target_ids = set(
                requests.filtered(lambda event: event.id not in consumed_request_ids).mapped(
                    "target_valuation_id"
                ).ids
            )
            submitted_new_ids = set(Event.search([
                ("event_type", "=", "valuation_revision_submitted"),
                ("new_valuation_id", "in", current_ids),
            ]).mapped("new_valuation_id").ids)
        for authorization in authorizations:
            valuation = current_by_order.get(authorization.order_id.id)
            if not valuation:
                continue
            if valuation.id in open_target_ids:
                indicators[authorization.id] = "revision_request"
            elif valuation.id in submitted_new_ids:
                indicators[authorization.id] = "submitted"
        for projection in self:
            projection.review_indicator = indicators.get(projection.id, False)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(f"""
            CREATE VIEW {self._table} AS (
                SELECT DISTINCT ON (a.vendor_id, a.order_id)
                    a.id, a.vendor_id,
                    o.order_number, o.borrower, o.service_type, o.property_type,
                    o.property_address, o.city, o.state, o.zip_code, o.bidding_round,
                    CASE WHEN a.active IS TRUE AND a.source = 'assignment'
                              AND o.status = 'engaged'
                              AND o.assigned_vendor_id = a.vendor_id
                              AND o.bidding_round = a.round_number
                              AND e.active IS TRUE
                              AND e.response_state = 'accepted'
                         THEN o.inspection_contact_name END AS inspection_contact_name,
                    CASE WHEN a.active IS TRUE AND a.source = 'assignment'
                              AND o.status = 'engaged'
                              AND o.assigned_vendor_id = a.vendor_id
                              AND o.bidding_round = a.round_number
                              AND e.active IS TRUE
                              AND e.response_state = 'accepted'
                         THEN o.inspection_contact_phone END AS inspection_contact_phone,
                    CASE WHEN a.active IS TRUE AND a.source = 'assignment'
                              AND o.status = 'engaged'
                              AND o.assigned_vendor_id = a.vendor_id
                              AND o.bidding_round = a.round_number
                              AND e.active IS TRUE
                              AND e.response_state = 'accepted'
                         THEN o.inspection_contact_email END AS inspection_contact_email,
                    CASE WHEN a.active IS NOT TRUE THEN 'declined_history'
                         ELSE a.source END AS vendor_phase,
                    CASE WHEN a.active IS NOT TRUE THEN 'declined'
                         WHEN a.source = 'invitation' THEN 'open_for_bid'
                         WHEN o.status IN ('assigned', 'engaged', 'report_received',
                              'reviewer_assigned', 'under_review', 'completed') THEN o.status
                         ELSE NULL END AS vendor_status,
                    o.status AS order_status,
                    CASE WHEN a.source = 'invitation' THEN a.expires_at END
                        AS response_deadline,
                    CASE WHEN a.source = 'assignment' THEN o.vendor_delivery_date END
                        AS vendor_delivery_date,
                    CASE WHEN a.source = 'assignment' THEN o.vendor_engaged_at END
                        AS vendor_engaged_at,
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
                        WHEN a.source = 'assignment' AND e.response_state = 'awaiting_acceptance'
                            THEN 'Awaiting Acceptance'
                        WHEN a.source = 'assignment' AND e.response_state = 'delivery_change_requested'
                            THEN 'Change Requested'
                        WHEN a.source = 'assignment' AND e.response_state = 'accepted'
                            THEN 'Accepted'
                        WHEN a.source = 'assignment' AND e.response_state = 'declined'
                            THEN 'Declined'
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
                    e.response_state AS engagement_response_state,
                    CASE e.response_state
                        WHEN 'awaiting_acceptance' THEN 'Awaiting Acceptance'
                        WHEN 'delivery_change_requested' THEN 'Change Requested'
                        WHEN 'accepted' THEN 'Accepted'
                        WHEN 'declined' THEN 'Declined'
                        ELSE NULL
                    END AS engagement_response_label,
                    pending.requested_delivery_date AS pending_requested_delivery_date,
                    pending.reason AS pending_request_reason,
                    e.decline_reason AS engagement_decline_reason,
                    company.currency_id AS currency_id,
                    (a.active IS TRUE AND a.source = 'invitation'
                     AND i.state = 'invited' AND o.status = 'bid_requested'
                     AND i.requested_delivery_date IS NOT NULL
                     AND i.standard_fee IS NOT NULL
                     AND i.round_number = o.bidding_round
                     AND (i.response_deadline IS NULL
                          OR CURRENT_TIMESTAMP AT TIME ZONE 'UTC' <= i.response_deadline)
                     AND (b.id IS NULL OR b.status = 'submitted')) AS can_respond,
                    (a.active IS TRUE AND a.source = 'assignment'
                     AND o.status = 'engaged' AND e.active IS TRUE
                     AND e.response_state = 'awaiting_acceptance')
                        AS can_accept_engagement,
                    (a.active IS TRUE AND a.source = 'assignment'
                     AND o.status = 'engaged' AND e.active IS TRUE
                     AND e.response_state = 'awaiting_acceptance')
                        AS can_request_delivery_change,
                    (a.active IS TRUE AND a.source = 'assignment'
                     AND o.status = 'engaged' AND e.active IS TRUE
                     AND e.response_state = 'awaiting_acceptance')
                        AS can_decline_engagement
                FROM trucalc_order_vendor_authorization a
                JOIN trucalc_order o ON o.id = a.order_id
                JOIN trucalc_vendor v ON v.id = a.vendor_id
                JOIN res_company company ON company.id = o.company_id
                LEFT JOIN trucalc_vendor_engagement e
                    ON e.order_id = a.order_id
                   AND e.vendor_id = a.vendor_id
                   AND e.company_id = a.company_id
                   AND e.round_number = a.round_number
                   AND e.assignment_authorization_id = a.id
                   AND e.active IS TRUE
                LEFT JOIN trucalc_vendor_engagement_event pending
                    ON pending.id = e.pending_request_event_id
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
                       OR o.status IN ('assigned', 'engaged', 'report_received',
                           'reviewer_assigned', 'under_review', 'completed'))
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

    def _trusted_engagement(self):
        self.ensure_one()
        vendor = self.env["trucalc.bid.invitation"]._vendor_identity()
        authorization = self.env["trucalc.order.vendor.authorization"].sudo().browse(
            self.id
        ).exists()
        if (
            not authorization or not authorization.active
            or authorization.source != "assignment"
            or authorization.vendor_id != vendor
        ):
            raise AccessError(_("Engagement access is not authorized."))
        engagement = self.env["trucalc.vendor.engagement"].sudo().search([
            ("assignment_authorization_id", "=", authorization.id),
            ("active", "=", True),
        ])
        order = authorization.order_id
        if (
            len(engagement) != 1
            or engagement.vendor_id != vendor
            or engagement.order_id != order
            or engagement.company_id != authorization.company_id
            or engagement.round_number != authorization.round_number
            or order.status != "engaged"
            or order.assigned_vendor_id != vendor
            or order.bidding_round != authorization.round_number
        ):
            raise AccessError(_("Engagement access is not authorized."))
        return engagement.with_user(self.env.user)

    def action_vendor_accept_engagement(self):
        return self._trusted_engagement().action_vendor_accept()

    def action_vendor_request_delivery_change(self, requested_date, reason):
        return self._trusted_engagement().action_vendor_request_delivery_change(
            requested_date, reason
        )

    def action_vendor_decline_engagement(self, reason):
        return self._trusted_engagement().action_vendor_decline(reason)

    @api.model_create_multi
    def create(self, vals_list):
        raise AccessError(_("Vendor Orders are read-only."))

    def write(self, vals):
        raise AccessError(_("Vendor Orders are read-only."))

    def unlink(self):
        raise AccessError(_("Vendor Orders are read-only."))
