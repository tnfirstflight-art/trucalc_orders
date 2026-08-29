from odoo import api, fields, models, _
from odoo.exceptions import AccessError


class TruCalcDocumentEvent(models.Model):
    _name = "trucalc.document.event"
    _description = "Immutable Document Event"
    _order = "event_at desc, id desc"

    event_type = fields.Selection([
        ("uploaded", "Uploaded"), ("tag_changed", "Tag Changed"),
        ("visibility_changed", "Visibility Changed"), ("deleted", "Deleted"),
    ], required=True, readonly=True, index=True)
    document_id = fields.Many2one("trucalc.document", readonly=True, index=True, ondelete="set null")
    stable_document_id = fields.Integer(required=True, readonly=True, index=True)
    order_id = fields.Many2one("trucalc.order", required=True, readonly=True, index=True, ondelete="restrict")
    company_id = fields.Many2one("res.company", required=True, readonly=True, index=True, ondelete="restrict")
    filename = fields.Char(required=True, readonly=True)
    tag_id = fields.Many2one("trucalc.document.tag", required=True, readonly=True, ondelete="restrict")
    prior_tag_id = fields.Many2one("trucalc.document.tag", readonly=True, ondelete="restrict")
    origin = fields.Selection([("bank", "Bank"), ("trucalc", "TruCalc")], required=True, readonly=True)
    originating_bank_id = fields.Many2one("res.company", readonly=True, ondelete="restrict")
    actor_id = fields.Many2one("res.users", required=True, readonly=True, index=True, ondelete="restrict")
    event_at = fields.Datetime(required=True, readonly=True, index=True)
    prior_visible_before_engagement = fields.Boolean(readonly=True)
    new_visible_before_engagement = fields.Boolean(readonly=True)
    prior_visible_after_engagement = fields.Boolean(readonly=True)
    new_visible_after_engagement = fields.Boolean(readonly=True)
    trucalc_notification_worthy = fields.Boolean(readonly=True, index=True)
    vendor_notification_worthy = fields.Boolean(readonly=True, index=True)
    vendor_id = fields.Many2one("trucalc.vendor", readonly=True, ondelete="restrict")
    authorization_id = fields.Many2one("trucalc.order.vendor.authorization", readonly=True, ondelete="restrict")
    engagement_id = fields.Many2one("trucalc.vendor.engagement", readonly=True, ondelete="restrict")
    bidding_round = fields.Integer(readonly=True)

    @api.model
    @api.private
    def _log_document_event(self, document, event_type, actor, **extra):
        document.ensure_one()
        values = {
            "event_type": event_type, "document_id": document.id,
            "stable_document_id": document.id, "order_id": document.order_id.id,
            "company_id": document.company_id.id, "filename": document.filename,
            "tag_id": document.tag_id.id, "origin": document.origin,
            "originating_bank_id": document.originating_bank_id.id or False,
            "actor_id": actor.id, "event_at": fields.Datetime.now(), **extra,
        }
        return super(TruCalcDocumentEvent, self.sudo()).create(values)

    @api.model_create_multi
    def create(self, vals_list):
        raise AccessError(_("Document events can only be created by trusted workflows."))

    def write(self, vals):
        raise AccessError(_("Document events are immutable."))

    def unlink(self):
        raise AccessError(_("Document events are immutable."))
