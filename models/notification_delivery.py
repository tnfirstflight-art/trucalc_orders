import logging
import smtplib
import socket
import time
import uuid
from datetime import timedelta
from urllib.parse import quote, urlsplit, urlunsplit

from psycopg2 import Error as PsycopgError
from psycopg2 import errors

from odoo import api, fields, models, tools, _
from odoo.addons.base.models.ir_mail_server import MailDeliveryException
from odoo.exceptions import AccessError, ValidationError


_logger = logging.getLogger(__name__)

NOTIFICATION_TYPE = "bank.order.completed.v1"
CHANNEL_EMAIL = "email"
PARAM_CAPTURE = "trucalc_orders.notifications.phase_a_capture_enabled"
PARAM_DISPATCH = "trucalc_orders.notifications.phase_a_dispatch_enabled"
PARAM_ACTIVATION = "trucalc_orders.notifications.phase_a_activation_at"
PARAM_FROM = "trucalc_orders.notifications.from_email"
PARAM_REPLY_TO = "trucalc_orders.notifications.reply_to"
PARAM_ALLOWED_HOST = "trucalc_orders.notifications.allowed_host"
TEMPLATE_XMLID = "trucalc_orders.mail_template_bank_order_completed_v1"

STATE_SELECTION = [
    ("pending", "Pending"),
    ("processing", "Processing"),
    ("sent", "Accepted by Outbound Transport"),
    ("retryable_failure", "Retry Scheduled"),
    ("permanent_failure", "Permanent Failure"),
    ("skipped_ineligible", "Skipped — Recipient Ineligible"),
    ("unknown", "Unknown — Manual Review Required"),
]

FAILURE_CATEGORY_SELECTION = [
    ("recipient_inactive", "Recipient Inactive"),
    ("recipient_role_changed", "Recipient Role Changed"),
    ("recipient_bank_changed", "Recipient Bank Changed"),
    ("recipient_no_access", "Recipient Lost Order Access"),
    ("recipient_email_missing", "Recipient Email Missing or Invalid"),
    ("recipient_email_changed_after_artifact", "Recipient Email Changed After Preparation"),
    ("tenant_mismatch", "Tenant Mismatch"),
    ("bank_inactive", "Bank Inactive"),
    ("event_integrity", "Event Integrity Failure"),
    ("template_contract", "Template Contract Failure"),
    ("sender_configuration", "Sender Configuration Failure"),
    ("portal_configuration", "Portal Configuration Failure"),
    ("smtp_connect_transient", "SMTP Connection Failure"),
    ("smtp_temporary_rejection", "SMTP Temporary Rejection"),
    ("smtp_permanent_rejection", "SMTP Permanent Rejection"),
    ("invalid_recipient", "SMTP Invalid Recipient"),
    ("rate_limited", "SMTP Rate Limited"),
    ("retry_budget_exhausted", "Retry Budget Exhausted"),
    ("timeout_ambiguous", "SMTP Outcome Ambiguous"),
    ("worker_lost_after_attempt", "Worker Lost After Attempt Started"),
    ("internal_integrity", "Internal Notification Integrity Failure"),
    ("operator_abandoned", "Operator Closed Without Resend"),
    ("unknown_safe", "Unknown Safe Category"),
]

FAILURE_STAGE_SELECTION = [
    ("eligibility", "Eligibility"),
    ("render", "Render"),
    ("connect", "Connect"),
    ("smtp", "SMTP"),
    ("persist", "Persistence"),
    ("worker", "Worker"),
    ("provider", "Provider"),
]

AUDIT_TYPE_SELECTION = [
    ("enqueued", "Enqueued"),
    ("claimed", "Claimed"),
    ("attempt_started", "Attempt Started"),
    ("accepted", "Transport Accepted"),
    ("transient_failure", "Transient Failure"),
    ("permanent_failure", "Permanent Failure"),
    ("skipped", "Skipped"),
    ("unknown", "Unknown"),
    ("manual_resend_approved", "Manual Resend Approved"),
    ("reconciled_accepted", "Provider Acceptance Confirmed"),
    ("reconciled_abandoned", "Closed Without Resend"),
]


class NotificationCaptureError(Exception):
    """A notification-only persistence failure that completion may isolate."""


class NotificationResolutionError(Exception):
    def __init__(self, category, stage="eligibility", terminal="skipped_ineligible"):
        super().__init__(category)
        self.category = category
        self.stage = stage
        self.terminal = terminal


class NotificationDelivery(models.Model):
    _name = "trucalc.notification.delivery"
    _description = "TruCalc Notification Delivery"
    _order = "enqueued_at desc, id desc"
    _rec_name = "order_number"

    notification_type = fields.Char(required=True, readonly=True, index=True)
    schema_version = fields.Integer(required=True, readonly=True, default=1)
    template_version = fields.Integer(required=True, readonly=True, default=1)
    channel = fields.Selection(
        [(CHANNEL_EMAIL, "Email")], required=True, readonly=True,
        default=CHANNEL_EMAIL, index=True,
    )
    event_id = fields.Many2one(
        "trucalc.order.lifecycle.event", required=True, readonly=True,
        index=True, ondelete="restrict",
    )
    order_id = fields.Many2one(
        "trucalc.order", required=True, readonly=True, index=True,
        ondelete="restrict",
    )
    company_id = fields.Many2one(
        "res.company", required=True, readonly=True, index=True,
        ondelete="restrict",
    )
    recipient_user_id = fields.Many2one(
        "res.users", required=True, readonly=True, index=True,
        ondelete="restrict",
    )
    order_number = fields.Char(required=True, readonly=True, index=True)
    idempotency_key = fields.Char(required=True, readonly=True, index=True)
    portal_url = fields.Char(compute="_compute_portal_url", readonly=True)

    state = fields.Selection(
        STATE_SELECTION, required=True, readonly=True, default="pending", index=True,
    )
    enqueued_at = fields.Datetime(required=True, readonly=True, index=True)
    next_attempt_at = fields.Datetime(readonly=True, index=True)
    attempt_count = fields.Integer(required=True, readonly=True, default=0)
    claim_token = fields.Char(readonly=True, index=True)
    claimed_at = fields.Datetime(readonly=True)
    lease_expires_at = fields.Datetime(readonly=True, index=True)
    attempt_started_at = fields.Datetime(readonly=True)
    first_attempt_at = fields.Datetime(readonly=True, index=True)
    last_attempt_at = fields.Datetime(readonly=True)
    transport_accepted_at = fields.Datetime(readonly=True, index=True)
    finalized_at = fields.Datetime(readonly=True, index=True)
    mail_id = fields.Many2one(
        "mail.mail", readonly=True, ondelete="set null", index=True,
        groups="trucalc_orders.group_trucalc_admin",
    )

    failure_category = fields.Selection(
        FAILURE_CATEGORY_SELECTION, readonly=True, index=True,
    )
    failure_stage = fields.Selection(FAILURE_STAGE_SELECTION, readonly=True)
    provider_reference = fields.Char(readonly=True)
    provider_delivery_state = fields.Selection([
        ("not_observed", "Not Observed"),
        ("delivered", "Provider Reports Delivered"),
        ("bounced", "Provider Reports Bounced"),
        ("complaint", "Provider Reports Complaint"),
    ], required=True, readonly=True, default="not_observed")
    provider_observed_at = fields.Datetime(readonly=True)
    reconciled_by_id = fields.Many2one(
        "res.users", readonly=True, ondelete="restrict",
    )
    reconciled_at = fields.Datetime(readonly=True)
    reconciliation_action = fields.Selection([
        ("confirmed_accepted", "Confirmed Accepted"),
        ("approved_resend", "Approved Resend"),
        ("abandoned", "Closed Without Resend"),
    ], readonly=True)
    reconciliation_reason = fields.Text(readonly=True)

    audit_ids = fields.One2many(
        "trucalc.notification.delivery.audit", "delivery_id", readonly=True,
    )

    _identity_unique = models.UniqueIndex(
        "(notification_type, event_id, recipient_user_id, channel)",
        "This notification event and recipient already have a delivery.",
    )
    _idempotency_unique = models.Constraint(
        "UNIQUE(idempotency_key)",
        "The notification idempotency key must be unique.",
    )
    _mail_unique = models.UniqueIndex(
        "(mail_id) WHERE mail_id IS NOT NULL",
        "A native mail artifact may belong to only one notification delivery.",
    )
    _attempt_nonnegative = models.Constraint(
        "CHECK(attempt_count >= 0)", "Notification attempt count cannot be negative.",
    )
    _processing_claim = models.Constraint(
        """CHECK(
            (state = 'processing' AND claim_token IS NOT NULL
             AND claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL)
            OR
            (state != 'processing' AND claim_token IS NULL
             AND claimed_at IS NULL AND lease_expires_at IS NULL)
        )""",
        "Notification processing claims must be complete and state-scoped.",
    )
    _terminal_time = models.Constraint(
        """CHECK(
            (state IN ('sent', 'permanent_failure', 'skipped_ineligible')
             AND finalized_at IS NOT NULL)
            OR
            (state NOT IN ('sent', 'permanent_failure', 'skipped_ineligible')
             AND finalized_at IS NULL)
        )""",
        "Notification finalization time does not match its state.",
    )
    _sent_acceptance = models.Constraint(
        "CHECK(state != 'sent' OR transport_accepted_at IS NOT NULL)",
        "A sent notification requires a transport acceptance time.",
    )

    @api.depends("order_number")
    def _compute_portal_url(self):
        for delivery in self:
            try:
                delivery.portal_url = delivery._trusted_portal_url()
            except NotificationResolutionError:
                delivery.portal_url = False

    @api.constrains(
        "notification_type", "channel", "event_id", "order_id", "company_id",
        "recipient_user_id", "order_number", "idempotency_key",
    )
    def _check_provenance(self):
        for delivery in self:
            delivery._validate_static_provenance()

    @api.model_create_multi
    def create(self, vals_list):
        raise AccessError(_("Notification deliveries require a trusted workflow."))

    def write(self, vals):
        raise AccessError(_("Notification deliveries require a trusted workflow."))

    def unlink(self):
        raise AccessError(_("Notification deliveries cannot be deleted."))

    def copy(self, default=None):
        raise AccessError(_("Notification deliveries cannot be copied."))

    @api.private
    def _controlled_write(self, values):
        return super(NotificationDelivery, self.sudo()).write(values)

    @api.model
    @api.private
    def _controlled_create(self, values):
        return super(NotificationDelivery, self.sudo()).create(values)

    @api.model
    @api.private
    def _registry(self):
        return {
            NOTIFICATION_TYPE: {
                "source_event": "order_completed",
                "channel": CHANNEL_EMAIL,
                "template": TEMPLATE_XMLID,
                "resolver": "_resolve_bank_order_completed_v1",
                "mandatory": True,
            },
        }

    @api.model
    @api.private
    def _parameter(self, key):
        return self.env["ir.config_parameter"].sudo().get_param(key)

    @api.model
    @api.private
    def _flag(self, key):
        return str(self._parameter(key) or "").strip().casefold() in {
            "1", "true", "yes", "on",
        }

    @api.model
    @api.private
    def _activation_at(self):
        raw = self._parameter(PARAM_ACTIVATION)
        if not raw:
            return False
        try:
            return fields.Datetime.to_datetime(raw)
        except (TypeError, ValueError):
            _logger.error("Phase A notification activation timestamp is invalid.")
            return False

    @api.private
    def _validate_static_provenance(self):
        self.ensure_one()
        event = self.event_id.sudo()
        order = self.order_id.sudo()
        spec = self._registry().get(self.notification_type)
        if (
            not spec or self.channel != spec["channel"]
            or event.event_type != spec["source_event"]
            or event.order_id != order or event.stable_order_id != order.id
            or event.company_id != self.company_id
            or order.company_id != self.company_id
            or self.recipient_user_id != order.requestor_id
            or self.order_number != order.order_number
            or not self.idempotency_key
        ):
            raise ValidationError(_("Notification delivery provenance is invalid."))
        return True

    @api.model
    @api.private
    def _idempotency_key(self, event, recipient):
        return "%s:%s:%s:%s" % (
            NOTIFICATION_TYPE, event.id, recipient.id, CHANNEL_EMAIL,
        )

    @api.model
    @api.private
    def _get_or_create_completion_delivery(self, event, recipient):
        event = event.sudo().exists()
        recipient = recipient.sudo().with_context(active_test=False).exists()
        if len(event) != 1 or len(recipient) != 1:
            raise NotificationCaptureError("missing completion event or requestor")
        order = event.order_id.sudo()
        identity = self._idempotency_key(event, recipient)
        domain = [
            ("notification_type", "=", NOTIFICATION_TYPE),
            ("event_id", "=", event.id),
            ("recipient_user_id", "=", recipient.id),
            ("channel", "=", CHANNEL_EMAIL),
        ]
        existing = self.sudo().search(domain, limit=1)
        if existing:
            existing._validate_static_provenance()
            if existing.idempotency_key != identity:
                raise ValidationError(_("Notification idempotency provenance is invalid."))
            return existing
        values = {
            "notification_type": NOTIFICATION_TYPE,
            "schema_version": 1,
            "template_version": 1,
            "channel": CHANNEL_EMAIL,
            "event_id": event.id,
            "order_id": order.id,
            "company_id": order.company_id.id,
            "recipient_user_id": recipient.id,
            "order_number": order.order_number,
            "idempotency_key": identity,
            "state": "pending",
            "enqueued_at": fields.Datetime.now(),
            "next_attempt_at": fields.Datetime.now(),
        }
        try:
            with self.env.cr.savepoint():
                delivery = self._controlled_create(values)
                delivery._append_audit("enqueued", False, "pending")
                return delivery
        except errors.UniqueViolation:
            existing = self.sudo().search(domain, limit=1)
            if not existing:
                raise
            existing._validate_static_provenance()
            return existing

    @api.model
    @api.private
    def _validate_capture_event(self, event, recipient):
        event = event.sudo().exists()
        if (
            len(event) != 1 or event.event_type != "order_completed"
            or event.stable_order_id != event.order_id.id
            or event.company_id != event.order_id.company_id
            or event.from_status != "under_review" or event.to_status != "completed"
            or event.order_id.status != "completed"
            or len(recipient.sudo().with_context(active_test=False).exists()) != 1
            or event.order_id.requestor_id != recipient
        ):
            raise ValidationError(_("Completion notification source provenance is invalid."))
        return event

    @api.model
    @api.private
    def _capture_completion_intent_nonblocking(self, event, original_recipient):
        if not self._flag(PARAM_CAPTURE):
            return self.browse()
        activation_at = self._activation_at()
        if not activation_at:
            return self.browse()
        # Internal orders have no Bank requestor and are outside this typed
        # notification's eligibility boundary.  Their completion remains valid.
        if not original_recipient:
            return self.browse()
        event = self._validate_capture_event(event, original_recipient)
        if event.event_at < activation_at:
            return self.browse()
        try:
            with self.env.cr.savepoint():
                return self._get_or_create_completion_delivery(
                    event, original_recipient,
                )
        except (NotificationCaptureError, PsycopgError) as error:
            _logger.error(
                "Phase A notification capture failed for order_id=%s event_id=%s class=%s; reconciliation required.",
                event.order_id.id, event.id, type(error).__name__,
            )
            return self.browse()

    @api.model
    @api.private
    def _reconcile_missing_completion_intents(self, limit=100):
        counters = {"eligible_events": 0, "missing": 0, "created": 0, "errors": 0}
        if not self._flag(PARAM_CAPTURE):
            return counters
        activation_at = self._activation_at()
        if not activation_at:
            return counters
        events = self.env["trucalc.order.lifecycle.event"].sudo().search([
            ("event_type", "=", "order_completed"),
            ("event_at", ">=", activation_at),
        ], order="event_at, id", limit=limit)
        events = events.filtered(lambda event: bool(event.order_id.requestor_id))
        counters["eligible_events"] = len(events)
        existing_event_ids = set(self.sudo().search([
            ("notification_type", "=", NOTIFICATION_TYPE),
            ("event_id", "in", events.ids),
            ("channel", "=", CHANNEL_EMAIL),
        ]).mapped("event_id").ids)
        for event in events.filtered(lambda item: item.id not in existing_event_ids):
            counters["missing"] += 1
            recipient = event.order_id.requestor_id
            if not recipient:
                counters["errors"] += 1
                continue
            try:
                with self.env.cr.savepoint():
                    self._validate_capture_event(event, recipient)
                    self._get_or_create_completion_delivery(event, recipient)
                counters["created"] += 1
            except (NotificationCaptureError, PsycopgError, ValidationError):
                counters["errors"] += 1
        if counters["missing"] or counters["errors"]:
            _logger.warning(
                "Phase A notification reconciliation: eligible=%s missing=%s created=%s errors=%s",
                counters["eligible_events"], counters["missing"],
                counters["created"], counters["errors"],
            )
        return counters

    @api.private
    def _resolve_bank_order_completed_v1(self):
        self.ensure_one()
        try:
            self._validate_static_provenance()
        except ValidationError as error:
            raise NotificationResolutionError(
                "event_integrity", terminal="permanent_failure",
            ) from error
        delivery = self.sudo()
        order = delivery.order_id.sudo()
        event = delivery.event_id.sudo()
        valuation = event.target_valuation_id.sudo().exists()
        Event = self.env["trucalc.order.lifecycle.event"].sudo()
        completion_count = Event.search_count([
            ("order_id", "=", order.id), ("event_type", "=", "order_completed"),
        ])
        approval = Event._valuation_approval(valuation) if valuation else Event.browse()
        if (
            order.status != "completed" or completion_count != 1
            or len(valuation) != 1 or valuation.order_id != order
            or valuation.company_id != order.company_id
            or valuation.artifact_type != "valuation"
            or valuation.status != "submitted" or not valuation.is_current
            or valuation != order.current_valuation_id
            or not approval or approval.target_valuation_id != valuation
            or approval.order_id != order or approval.company_id != order.company_id
            or approval.actor_id != order.reviewer_user_id
            or event.target_valuation_id != valuation
        ):
            raise NotificationResolutionError(
                "event_integrity", terminal="permanent_failure",
            )
        recipient = delivery.recipient_user_id.sudo().with_context(active_test=False).exists()
        if not recipient or not recipient.active:
            raise NotificationResolutionError("recipient_inactive")
        if recipient != order.requestor_id:
            raise NotificationResolutionError("tenant_mismatch")
        role = recipient._trucalc_bank_role_key()
        if role not in ("requestor", "administrator"):
            raise NotificationResolutionError("recipient_role_changed")
        try:
            bank = recipient._trucalc_bank_identity()
        except AccessError as error:
            raise NotificationResolutionError("recipient_bank_changed") from error
        if bank != order.company_id:
            raise NotificationResolutionError("tenant_mismatch")
        if not bank.trucalc_bank_active:
            raise NotificationResolutionError("bank_inactive")
        try:
            order.with_user(recipient).check_access("read")
        except AccessError as error:
            raise NotificationResolutionError("recipient_no_access") from error
        try:
            email = recipient._trucalc_normalize_login(recipient.email)
            login = recipient._trucalc_normalize_login(recipient.login)
        except ValidationError as error:
            raise NotificationResolutionError("recipient_email_missing") from error
        if email != login:
            raise NotificationResolutionError("recipient_email_missing")
        return {"recipient": recipient, "bank": bank, "email": email}

    @api.model
    @api.private
    def _single_configured_email(self, key):
        value = (self._parameter(key) or "").strip()
        if not value or any(character in value for character in "\r\n"):
            raise NotificationResolutionError(
                "sender_configuration", stage="render", terminal="permanent_failure",
            )
        addresses = tools.email_split(value)
        normalized = tools.email_normalize(value)
        if len(addresses) != 1 or not normalized or value.casefold() != normalized:
            raise NotificationResolutionError(
                "sender_configuration", stage="render", terminal="permanent_failure",
            )
        return normalized

    @api.private
    def _trusted_portal_url(self):
        self.ensure_one()
        base_value = (self._parameter("web.base.url") or "").strip()
        allowed_host = (self._parameter(PARAM_ALLOWED_HOST) or "").strip().casefold()
        if not base_value or not allowed_host or any(c in base_value for c in "\r\n"):
            raise NotificationResolutionError(
                "portal_configuration", stage="render", terminal="permanent_failure",
            )
        parsed = urlsplit(base_value)
        if (
            parsed.scheme.casefold() != "https" or not parsed.hostname
            or parsed.hostname.casefold() != allowed_host
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.port not in (None, 443)
        ):
            raise NotificationResolutionError(
                "portal_configuration", stage="render", terminal="permanent_failure",
            )
        order_number = self.order_number or ""
        if (
            not order_number or len(order_number) > 64
            or any(ord(character) < 32 for character in order_number)
        ):
            raise NotificationResolutionError(
                "template_contract", stage="render", terminal="permanent_failure",
            )
        prefix = parsed.path.rstrip("/")
        path = "%s/my/trucalc/bank/orders/%s/documents" % (
            prefix, quote(order_number, safe=""),
        )
        return urlunsplit(("https", parsed.netloc, path, "", ""))

    @api.private
    def _sender_configuration(self):
        return {
            "email_from": tools.formataddr((
                "TruCalc Valuation Solutions",
                self._single_configured_email(PARAM_FROM),
            )),
            "reply_to": self._single_configured_email(PARAM_REPLY_TO),
        }

    @api.private
    def _append_audit(
        self, audit_type, prior_state, new_state, *, category=False,
        actor=False, evidence=False, reason=False, attempt_number=None,
    ):
        self.ensure_one()
        return self.env["trucalc.notification.delivery.audit"]._controlled_append({
            "delivery_id": self.id,
            "company_id": self.company_id.id,
            "event_at": fields.Datetime.now(),
            "audit_type": audit_type,
            "attempt_number": self.attempt_count if attempt_number is None else attempt_number,
            "prior_state": prior_state or False,
            "new_state": new_state or False,
            "failure_category": category or False,
            "actor_user_id": actor.id if actor else False,
            "claim_token": self.claim_token or False,
            "evidence_reference": evidence or False,
            "reason": reason or False,
        })

    @api.model
    @api.private
    def _claim_next_due(self, lease_minutes=10):
        now = fields.Datetime.now()
        self.env.cr.execute(
            """
            SELECT id
              FROM trucalc_notification_delivery
             WHERE state IN ('pending', 'retryable_failure')
               AND next_attempt_at IS NOT NULL
               AND next_attempt_at <= %s
             ORDER BY next_attempt_at, id
             FOR UPDATE SKIP LOCKED
             LIMIT 1
            """,
            (now,),
        )
        row = self.env.cr.fetchone()
        if not row:
            return self.browse()
        delivery = self.sudo().browse(row[0]).exists()
        token = uuid.uuid4().hex
        prior = delivery.state
        delivery._controlled_write({
            "state": "processing",
            "claim_token": token,
            "claimed_at": now,
            "lease_expires_at": now + timedelta(minutes=lease_minutes),
            "attempt_started_at": False,
            "next_attempt_at": False,
            "failure_category": False,
            "failure_stage": False,
            "finalized_at": False,
        })
        delivery._append_audit("claimed", prior, "processing")
        return delivery

    @api.model
    @api.private
    def _recover_expired_claims(self):
        now = fields.Datetime.now()
        expired = self.sudo().search([
            ("state", "=", "processing"),
            ("lease_expires_at", "<", now),
        ])
        counters = {"reclaimed": 0, "unknown": 0}
        for delivery in expired:
            self.env.cr.execute(
                "SELECT id FROM trucalc_notification_delivery WHERE id = %s FOR UPDATE",
                (delivery.id,),
            )
            delivery.invalidate_recordset()
            if delivery.state != "processing" or not delivery.lease_expires_at or delivery.lease_expires_at >= now:
                continue
            if delivery.attempt_started_at:
                prior = delivery.state
                delivery._controlled_write({
                    "state": "unknown",
                    "claim_token": False, "claimed_at": False,
                    "lease_expires_at": False,
                    "failure_category": "worker_lost_after_attempt",
                    "failure_stage": "worker",
                })
                delivery._append_audit(
                    "unknown", prior, "unknown",
                    category="worker_lost_after_attempt",
                )
                counters["unknown"] += 1
            else:
                delivery._controlled_write({
                    "state": "pending",
                    "claim_token": False, "claimed_at": False,
                    "lease_expires_at": False,
                    "next_attempt_at": now,
                })
                counters["reclaimed"] += 1
        return counters

    @api.private
    def _finalize_without_attempt(self, resolution_error):
        self.ensure_one()
        prior = self.state
        now = fields.Datetime.now()
        values = {
            "state": resolution_error.terminal,
            "claim_token": False, "claimed_at": False,
            "lease_expires_at": False,
            "failure_category": resolution_error.category,
            "failure_stage": resolution_error.stage,
            "finalized_at": now,
        }
        self._controlled_write(values)
        self._append_audit(
            "skipped" if resolution_error.terminal == "skipped_ineligible" else "permanent_failure",
            prior, resolution_error.terminal, category=resolution_error.category,
        )

    @api.private
    def _prepare_attempt(self):
        self.ensure_one()
        delivery = self.sudo()
        if delivery.state != "processing" or not delivery.claim_token:
            raise ValidationError(_("Notification claim is no longer valid."))
        try:
            resolved = delivery._resolve_bank_order_completed_v1()
            sender = delivery._sender_configuration()
            portal_url = delivery._trusted_portal_url()
        except NotificationResolutionError as error:
            delivery._finalize_without_attempt(error)
            return self.env["mail.mail"].browse()
        mail = delivery.mail_id.sudo().exists()
        if mail:
            existing = tools.email_normalize(mail.email_to or "")
            if existing != resolved["email"]:
                delivery._finalize_without_attempt(NotificationResolutionError(
                    "recipient_email_changed_after_artifact",
                    terminal="permanent_failure",
                ))
                return self.env["mail.mail"].browse()
        else:
            template = self.env.ref(TEMPLATE_XMLID).sudo().with_context(
                trucalc_portal_url=portal_url,
            )
            mail_id = template.send_mail(
                delivery.id, force_send=False, raise_exception=True,
                email_values={
                    "state": "cancel",
                    "auto_delete": False,
                    "email_from": sender["email_from"],
                    "reply_to": sender["reply_to"],
                    "email_to": resolved["email"],
                    "email_cc": False,
                    "recipient_ids": [],
                    "partner_ids": [],
                    "attachment_ids": [],
                },
            )
            mail = self.env["mail.mail"].sudo().browse(mail_id).exists()
            if (
                len(mail) != 1 or mail.state != "cancel"
                or tools.email_normalize(mail.email_to or "") != resolved["email"]
                or mail.email_cc or mail.recipient_ids or mail.attachment_ids
            ):
                raise NotificationResolutionError(
                    "template_contract", stage="render", terminal="permanent_failure",
                )
            delivery._controlled_write({"mail_id": mail.id})
        now = fields.Datetime.now()
        attempt = delivery.attempt_count + 1
        delivery._controlled_write({
            "attempt_count": attempt,
            "attempt_started_at": now,
            "first_attempt_at": delivery.first_attempt_at or now,
            "last_attempt_at": now,
        })
        delivery._append_audit(
            "attempt_started", "processing", "processing",
            attempt_number=attempt,
        )
        return mail

    @api.model
    @api.private
    def _classify_transport_exception(self, error):
        if isinstance(error, MailDeliveryException):
            nested = next(
                (item for item in error.args if isinstance(item, Exception)),
                None,
            )
            if nested:
                return self._classify_transport_exception(nested)
        if isinstance(error, (ConnectionRefusedError, socket.gaierror, smtplib.SMTPConnectError)):
            return "retryable", "smtp_connect_transient", "connect"
        if isinstance(error, smtplib.SMTPRecipientsRefused):
            return "permanent", "invalid_recipient", "smtp"
        if isinstance(error, smtplib.SMTPDataError):
            if 400 <= error.smtp_code < 500:
                return "retryable", "smtp_temporary_rejection", "smtp"
            return "permanent", "smtp_permanent_rejection", "smtp"
        if isinstance(error, smtplib.SMTPResponseException):
            if 400 <= error.smtp_code < 500:
                return "retryable", "smtp_temporary_rejection", "smtp"
            return "permanent", "smtp_permanent_rejection", "smtp"
        return "unknown", "timeout_ambiguous", "smtp"

    @api.private
    def _clear_claim_values(self):
        return {"claim_token": False, "claimed_at": False, "lease_expires_at": False}

    @api.private
    def _finalize_transport_result(self, outcome, category=False, stage=False):
        self.ensure_one()
        now = fields.Datetime.now()
        prior = self.state
        if self.state != "processing":
            raise ValidationError(_("Notification attempt is no longer current."))
        if outcome == "sent":
            values = {
                "state": "sent", "transport_accepted_at": now,
                "finalized_at": now, "failure_category": False,
                "failure_stage": False, **self._clear_claim_values(),
            }
            audit_type = "accepted"
        elif outcome == "retryable":
            if self.attempt_count >= 4:
                outcome = "permanent"
                category = "retry_budget_exhausted"
            else:
                delays = {1: 1, 2: 5, 3: 20}
                values = {
                    "state": "retryable_failure",
                    "next_attempt_at": now + timedelta(minutes=delays[self.attempt_count]),
                    "failure_category": category,
                    "failure_stage": stage,
                    **self._clear_claim_values(),
                }
                self._controlled_write(values)
                self._append_audit(
                    "transient_failure", prior, "retryable_failure",
                    category=category,
                )
                return
        if outcome == "permanent":
            values = {
                "state": "permanent_failure", "finalized_at": now,
                "failure_category": category,
                "failure_stage": stage, **self._clear_claim_values(),
            }
            audit_type = "permanent_failure"
        elif outcome == "unknown":
            values = {
                "state": "unknown", "failure_category": category,
                "failure_stage": stage, **self._clear_claim_values(),
            }
            audit_type = "unknown"
        self._controlled_write(values)
        self._append_audit(audit_type, prior, values["state"], category=category)

    @api.private
    def _execute_attempt(self):
        self.ensure_one()
        delivery = self.sudo()
        mail = delivery.mail_id.sudo().exists()
        if delivery.state != "processing" or not delivery.attempt_started_at or len(mail) != 1:
            raise ValidationError(_("Notification attempt is not prepared."))
        try:
            # The artifact remains in ``cancel`` while the attempt marker is
            # committed.  Expose it as outgoing only inside this uncommitted
            # transport transaction, so Odoo's native cron can never claim it.
            mail.write({"state": "outgoing"})
            mail.send(raise_exception=True)
            mail.invalidate_recordset(["state", "failure_type", "failure_reason"])
            if mail.state != "sent":
                delivery._finalize_transport_result(
                    "permanent", "internal_integrity", "persist",
                )
            else:
                delivery._finalize_transport_result("sent")
        except PsycopgError:
            raise
        except Exception as error:
            # A confirmed failure or ambiguous result must never leave the
            # native queue eligible to retry this business notification.
            mail.write({"state": "cancel"})
            outcome, category, stage = self._classify_transport_exception(error)
            delivery._finalize_transport_result(outcome, category, stage)

    @api.model
    @api.private
    def _cron_process_phase_a(self, batch_size=50, budget_seconds=40):
        reconciliation = self._reconcile_missing_completion_intents(limit=100)
        recovered = self._recover_expired_claims()
        self.env.cr.commit()
        if not self._flag(PARAM_DISPATCH):
            return {
                "reconciliation": reconciliation,
                "recovered": recovered,
                "processed": 0,
            }
        processed = 0
        started = time.monotonic()
        while processed < batch_size and time.monotonic() - started < budget_seconds:
            delivery = self._claim_next_due()
            if not delivery:
                break
            delivery_id = delivery.id
            self.env.cr.commit()
            try:
                delivery = self.sudo().browse(delivery_id).exists()
                mail = delivery._prepare_attempt()
                self.env.cr.commit()
                if mail:
                    delivery = self.sudo().browse(delivery_id).exists()
                    delivery._execute_attempt()
                    self.env.cr.commit()
            except (NotificationResolutionError, ValidationError) as error:
                self.env.cr.rollback()
                delivery = self.sudo().browse(delivery_id).exists()
                if delivery and delivery.state == "processing" and not delivery.attempt_started_at:
                    delivery._finalize_without_attempt(NotificationResolutionError(
                        "template_contract", stage="render", terminal="permanent_failure",
                    ))
                    self.env.cr.commit()
                _logger.error(
                    "Phase A notification preparation failed for delivery_id=%s class=%s",
                    delivery_id, type(error).__name__,
                )
            except PsycopgError:
                self.env.cr.rollback()
                _logger.exception(
                    "Phase A notification database failure for delivery_id=%s",
                    delivery_id,
                )
            except Exception as error:
                # Rendering/configuration failures occur before the committed
                # attempt marker and can terminate safely.  Any unexpected
                # post-marker failure retains the durable lease so expiry moves
                # it to unknown instead of risking an automatic duplicate.
                self.env.cr.rollback()
                delivery = self.sudo().browse(delivery_id).exists()
                if (
                    delivery and delivery.state == "processing"
                    and not delivery.attempt_started_at
                ):
                    delivery._finalize_without_attempt(NotificationResolutionError(
                        "template_contract", stage="render",
                        terminal="permanent_failure",
                    ))
                    self.env.cr.commit()
                _logger.error(
                    "Phase A notification worker failure for delivery_id=%s class=%s",
                    delivery_id, type(error).__name__,
                )
            processed += 1
        pending_age = self.sudo().search([
            ("state", "in", ("pending", "retryable_failure")),
        ], order="enqueued_at", limit=1)
        unknown_count = self.sudo().search_count([("state", "=", "unknown")])
        if unknown_count or (
            pending_age and fields.Datetime.now() - pending_age.enqueued_at > timedelta(minutes=5)
        ):
            _logger.warning(
                "Phase A notification health: unknown=%s oldest_pending_id=%s",
                unknown_count, pending_age.id or 0,
            )
        return {
            "reconciliation": reconciliation,
            "recovered": recovered,
            "processed": processed,
            "unknown": unknown_count,
        }

    @api.private
    def _require_notification_admin(self):
        self.ensure_one()
        actor = self.env.user
        if (
            not actor.active or actor.share or actor._trucalc_has_external_role()
            or not actor.has_group("trucalc_orders.group_trucalc_admin")
            or self.sudo().company_id not in actor.company_ids
        ):
            raise AccessError(_("Notification reconciliation is not authorized."))
        self.with_user(actor).check_access("read")
        return actor

    def action_open_reconciliation_wizard(self):
        self.ensure_one()
        self._require_notification_admin()
        if self.state != "unknown":
            raise ValidationError(_("Only an unknown delivery may be reconciled."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Reconcile Notification"),
            "res_model": "trucalc.notification.reconcile.wizard",
            "view_mode": "form",
            "view_id": self.env.ref(
                "trucalc_orders.view_trucalc_notification_reconcile_wizard_form"
            ).id,
            "target": "new",
            "context": {"default_delivery_id": self.id},
        }

    @api.private
    def _reconcile_unknown(self, action, reason, evidence=False, accepted_at=False):
        self.ensure_one()
        actor = self._require_notification_admin()
        reason = reason.strip() if isinstance(reason, str) else ""
        evidence = evidence.strip() if isinstance(evidence, str) else ""
        if not reason or len(reason) > 2000 or len(evidence) > 255:
            raise ValidationError(_("A concise reconciliation reason is required."))
        self.env.cr.execute(
            "SELECT id FROM trucalc_notification_delivery WHERE id = %s FOR UPDATE",
            (self.id,),
        )
        delivery = self.sudo()
        delivery.invalidate_recordset()
        if delivery.state != "unknown":
            raise ValidationError(_("This unknown delivery has already been reconciled."))
        now = fields.Datetime.now()
        common = {
            "reconciled_by_id": actor.id,
            "reconciled_at": now,
            "reconciliation_reason": reason,
            "provider_reference": evidence or False,
        }
        if action == "confirmed_accepted":
            try:
                accepted_at = fields.Datetime.to_datetime(accepted_at)
            except (TypeError, ValueError):
                accepted_at = False
            if not accepted_at or accepted_at > now:
                raise ValidationError(_("A valid transport acceptance time is required."))
            values = {
                **common, "state": "sent", "transport_accepted_at": accepted_at,
                "finalized_at": now, "reconciliation_action": action,
                "failure_category": False, "failure_stage": False,
                "provider_observed_at": now,
            }
            audit_type = "reconciled_accepted"
        elif action == "approved_resend":
            delivery._resolve_bank_order_completed_v1()
            if not evidence:
                raise ValidationError(_(
                    "Provider evidence of nonacceptance is required before resend approval."
                ))
            values = {
                **common, "state": "retryable_failure", "next_attempt_at": now,
                "attempt_started_at": False, "reconciliation_action": action,
                "failure_category": False, "failure_stage": False,
            }
            audit_type = "manual_resend_approved"
        elif action == "abandoned":
            values = {
                **common, "state": "permanent_failure", "finalized_at": now,
                "reconciliation_action": action,
                "failure_category": "operator_abandoned", "failure_stage": "provider",
            }
            audit_type = "reconciled_abandoned"
        else:
            raise ValidationError(_("The reconciliation action is invalid."))
        delivery._controlled_write(values)
        delivery._append_audit(
            audit_type, "unknown", values["state"],
            category=values.get("failure_category"), actor=actor,
            evidence=evidence, reason=reason,
        )
        return True


class NotificationDeliveryAudit(models.Model):
    _name = "trucalc.notification.delivery.audit"
    _description = "Immutable Notification Delivery Audit"
    _order = "event_at desc, id desc"

    delivery_id = fields.Many2one(
        "trucalc.notification.delivery", required=True, readonly=True,
        index=True, ondelete="restrict",
    )
    company_id = fields.Many2one(
        "res.company", required=True, readonly=True, index=True,
        ondelete="restrict",
    )
    event_at = fields.Datetime(required=True, readonly=True, index=True)
    audit_type = fields.Selection(
        AUDIT_TYPE_SELECTION, required=True, readonly=True, index=True,
    )
    attempt_number = fields.Integer(required=True, readonly=True, default=0)
    prior_state = fields.Selection(STATE_SELECTION, readonly=True)
    new_state = fields.Selection(STATE_SELECTION, readonly=True)
    failure_category = fields.Selection(FAILURE_CATEGORY_SELECTION, readonly=True)
    actor_user_id = fields.Many2one(
        "res.users", readonly=True, ondelete="restrict", index=True,
    )
    claim_token = fields.Char(readonly=True)
    evidence_reference = fields.Char(readonly=True)
    reason = fields.Text(readonly=True)

    _attempt_nonnegative = models.Constraint(
        "CHECK(attempt_number >= 0)", "Notification audit attempt cannot be negative.",
    )
    _company_matches = models.Constraint(
        "CHECK(company_id IS NOT NULL)", "Notification audit requires a company.",
    )

    @api.constrains("delivery_id", "company_id")
    def _check_delivery_company(self):
        for audit in self:
            if audit.company_id != audit.delivery_id.company_id:
                raise ValidationError(_("Notification audit company is invalid."))

    @api.model
    @api.private
    def _controlled_append(self, values):
        return super(NotificationDeliveryAudit, self.sudo()).create(values)

    @api.model_create_multi
    def create(self, vals_list):
        raise AccessError(_("Notification audits require a trusted workflow."))

    def write(self, vals):
        raise AccessError(_("Notification audits are immutable."))

    def unlink(self):
        raise AccessError(_("Notification audits cannot be deleted."))

    def copy(self, default=None):
        raise AccessError(_("Notification audits cannot be copied."))


class NotificationReconcileWizard(models.TransientModel):
    _name = "trucalc.notification.reconcile.wizard"
    _description = "Reconcile Unknown Notification"

    delivery_id = fields.Many2one(
        "trucalc.notification.delivery", required=True, readonly=True,
    )
    action = fields.Selection([
        ("confirmed_accepted", "Provider Confirmed Acceptance"),
        ("approved_resend", "Approve One Controlled Resend"),
        ("abandoned", "Close Without Resend"),
    ], required=True)
    accepted_at = fields.Datetime(string="Transport Accepted At")
    evidence_reference = fields.Char(string="Evidence Reference")
    reason = fields.Text(required=True)

    def action_apply(self):
        self.ensure_one()
        self.delivery_id._reconcile_unknown(
            self.action, self.reason,
            evidence=self.evidence_reference,
            accepted_at=self.accepted_at,
        )
        return {"type": "ir.actions.act_window_close"}
