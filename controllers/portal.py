import base64
import json
from datetime import datetime, time, timedelta
from urllib.parse import urlencode

from odoo import fields, http
from odoo.exceptions import AccessError, ValidationError
from odoo.fields import Domain
from odoo.http import request
from odoo.http import content_disposition

from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager

from ..models.document import MAX_DOCUMENT_BYTES
from ..models.res_users import (
    BANK_PORTAL_COLUMN_KEYS,
    VENDOR_PORTAL_COLUMN_KEYS,
)


BANK_PORTAL_COLUMNS = (
    ("order_number", "Order Number"),
    ("borrower", "Borrower"),
    ("property_address", "Property Address"),
    ("loan_number", "Loan Number"),
    ("loan_purpose", "Loan Purpose"),
    ("service_type", "Service Type"),
    ("status", "Status"),
    ("request_date", "Request Date"),
    ("anticipated_delivery_date", "Anticipated Delivery Date"),
    ("completion_date", "Completion Date"),
    ("fee", "Fee"),
)
VENDOR_PORTAL_COLUMNS = (
    ("order_number", "Order Number"),
    ("borrower", "Borrower"),
    ("property_address", "Property Address"),
    ("loan_purpose", "Loan Purpose"),
    ("service_type", "Service Type"),
    ("status", "Status"),
    ("invitation_date", "Invitation Date"),
    ("bid_due_date", "Bid Due Date"),
    ("agreed_vendor_fee", "Agreed Vendor Fee"),
    ("delivery_due_date", "Delivery Due Date"),
    ("response", "Response"),
)
BANK_PORTAL_DEFAULTS = {
    "administrator": (
        "order_number", "borrower", "property_address", "service_type",
        "status", "anticipated_delivery_date", "fee",
    ),
    "requestor": (
        "order_number", "borrower", "property_address", "loan_purpose",
        "service_type", "status", "anticipated_delivery_date",
    ),
    "view_only": (
        "order_number", "borrower", "property_address", "service_type",
        "status", "anticipated_delivery_date",
    ),
}
VENDOR_PORTAL_DEFAULTS = (
    "order_number", "borrower", "property_address", "loan_purpose",
    "service_type", "status", "delivery_due_date", "response",
)

assert tuple(key for key, _label in BANK_PORTAL_COLUMNS) == BANK_PORTAL_COLUMN_KEYS
assert tuple(key for key, _label in VENDOR_PORTAL_COLUMNS) == VENDOR_PORTAL_COLUMN_KEYS


class TruCalcVendorPortal(CustomerPortal):
    _portal_order_page_size = 20
    _portal_search_limit = 200
    _vendor_order_filter_keys = frozenset({
        "active", "submitted", "recent_completed", "all",
    })
    _bank_order_filter_keys = frozenset({
        "open", "recent_completed", "outstanding_invoices", "all",
    })
    _bank_sort_fields = {
        "newest": "create_date",
        "order_number": "order_number",
        "borrower": "borrower",
        "property_address": "property_address",
        "loan_number": "loan_number",
        "loan_purpose": "loan_purpose",
        "service_type": "service_type",
        "request_date": "order_date",
        "due_date": "due_date",
        "fee": "current_agreed_fee",
        "status": "status",
    }
    _vendor_sort_fields = {
        "newest": "authorization_date",
        "completed": "completion_at",
        "order_number": "order_number",
        "service_type": "service_type",
        "borrower": "borrower",
        "property_address": "property_address",
        "loan_purpose": "loan_purpose",
        "status": "vendor_status",
        "response": "vendor_response_label",
        "due_date": "delivery_due_date",
        "invitation_date": "invitation_date",
        "bid_due_date": "response_deadline",
        "agreed_vendor_fee": "agreed_vendor_fee",
    }

    _column_return_keys = frozenset({
        "filterby", "search", "status", "service", "date_field",
        "date_from", "date_to", "sort", "direction", "page",
    })

    def _portal_column_spec(self, kind):
        if kind == "bank":
            role = request.env.user._trucalc_bank_role_key()
            return {
                "columns": BANK_PORTAL_COLUMNS,
                "defaults": BANK_PORTAL_DEFAULTS.get(role, ("order_number",)),
                "field": "trucalc_bank_portal_columns",
                "route": "/my/trucalc/bank/columns",
                "base_url": "/my/trucalc/bank/orders",
                "filter_keys": self._bank_order_filter_keys,
                "sort_fields": self._bank_sort_fields,
            }
        return {
            "columns": VENDOR_PORTAL_COLUMNS,
            "defaults": VENDOR_PORTAL_DEFAULTS,
            "field": "trucalc_vendor_portal_columns",
            "route": "/my/trucalc/vendor/columns",
            "base_url": "/my/trucalc/orders",
            "filter_keys": self._vendor_order_filter_keys,
            "sort_fields": self._vendor_sort_fields,
        }

    def _normalize_portal_columns(self, kind, selected):
        spec = self._portal_column_spec(kind)
        selected = set(selected if isinstance(selected, (list, tuple)) else ())
        return tuple(
            key for key, _label in spec["columns"]
            if key == "order_number" or key in selected
        )

    def _portal_column_values(self, kind, page):
        spec = self._portal_column_spec(kind)
        saved = getattr(request.env.user.sudo(), spec["field"])
        selected = (
            self._normalize_portal_columns(kind, saved)
            if isinstance(saved, (list, tuple))
            else tuple(spec["defaults"])
        )
        return {
            "portal_columns": selected,
            "portal_column_options": tuple({
                "key": key,
                "label": label,
                "mandatory": key == "order_number",
            } for key, label in spec["columns"]),
            "portal_column_route": spec["route"],
            "portal_column_page": max(int(page), 1),
            "portal_column_menu_id": "trucalc-%s-column-menu" % kind,
        }

    def _portal_column_redirect(self, kind, form):
        spec = self._portal_column_spec(kind)
        filterby = (form.get("filterby") or "").strip()
        if filterby not in spec["filter_keys"]:
            filterby = "open" if kind == "bank" else "active"
        sort = (form.get("sort") or "").strip()
        if sort not in spec["sort_fields"]:
            sort = "order_number"
        direction = (form.get("direction") or "").strip().lower()
        if direction not in ("asc", "desc"):
            direction = "asc"
        values = {
            "filterby": filterby,
            "search": (form.get("search") or "").strip()[:self._portal_search_limit],
            "status": (form.get("status") or "").strip()[:64],
            "service": (form.get("service") or "").strip()[:64],
            "date_field": (form.get("date_field") or "").strip()[:64],
            "date_from": (form.get("date_from") or "").strip()[:10],
            "date_to": (form.get("date_to") or "").strip()[:10],
            "sort": sort,
            "direction": direction,
        }
        try:
            page = max(int(form.get("page") or 1), 1)
        except (TypeError, ValueError):
            page = 1
        base_url = spec["base_url"]
        if page > 1:
            base_url = "%s/page/%s" % (base_url, page)
        return self._portal_url(base_url, values)

    def _update_portal_columns(self, kind):
        form = request.httprequest.form
        if set(form) - (self._column_return_keys | {
            "csrf_token", "columns", "column_action",
        }):
            raise request.not_found()
        spec = self._portal_column_spec(kind)
        if form.get("column_action") == "reset":
            value = False
        else:
            value = list(self._normalize_portal_columns(
                kind, form.getlist("columns"),
            ))
        request.env.user.sudo().write({spec["field"]: value})
        return request.redirect(self._portal_column_redirect(kind, form))

    @http.route(
        "/my/trucalc/bank/columns", type="http", auth="user", website=True,
        methods=["POST"], csrf=True,
    )
    def portal_bank_columns(self, **post):
        if not self._is_trucalc_bank():
            raise request.not_found()
        try:
            request.env.user._trucalc_bank_identity()
        except AccessError:
            raise request.not_found()
        return self._update_portal_columns("bank")

    @http.route(
        "/my/trucalc/vendor/columns", type="http", auth="user", website=True,
        methods=["POST"], csrf=True,
    )
    def portal_vendor_columns(self, **post):
        if not self._is_trucalc_vendor():
            raise request.not_found()
        try:
            request.env.user._trucalc_vendor_identity()
        except AccessError:
            raise request.not_found()
        return self._update_portal_columns("vendor")

    def _selection_options(self, model_name, field_name):
        field = request.env[model_name]._fields[field_name]
        return field._description_selection(request.env)

    def _portal_list_query(
        self, kwargs, *, filterby, sort_fields, default_sort,
        default_direction, status_options, date_fields,
    ):
        search = (kwargs.get("search") or "").strip()[:self._portal_search_limit]
        status = (kwargs.get("status") or "").strip()
        service = (kwargs.get("service") or "").strip()
        date_field = (kwargs.get("date_field") or "").strip()
        raw_date_from = (kwargs.get("date_from") or "").strip()
        raw_date_to = (kwargs.get("date_to") or "").strip()
        invalid = False

        allowed_statuses = {item[0] for item in status_options}
        service_options = self._selection_options("trucalc.order", "service_type")
        allowed_services = {item[0] for item in service_options}
        if status and status not in allowed_statuses:
            invalid = True
        if service and service not in allowed_services:
            invalid = True
        if date_field and date_field not in date_fields:
            invalid = True

        date_from = date_to = False
        try:
            date_from = fields.Date.to_date(raw_date_from) if raw_date_from else False
            date_to = fields.Date.to_date(raw_date_to) if raw_date_to else False
        except (TypeError, ValueError):
            invalid = True
        if (raw_date_from and not date_from) or (raw_date_to and not date_to):
            invalid = True
        if (date_from or date_to) and not date_field:
            invalid = True
        if date_from and date_to and date_from > date_to:
            invalid = True

        requested_sort = (kwargs.get("sort") or "").strip()
        sort = requested_sort if requested_sort in sort_fields else default_sort
        requested_direction = (kwargs.get("direction") or "").strip().lower()
        direction = (
            requested_direction
            if requested_direction in ("asc", "desc")
            else default_direction if not requested_sort else "asc"
        )

        query = {
            "filterby": filterby,
            "search": search,
            "status": status if status in allowed_statuses else "",
            "service": service if service in allowed_services else "",
            "date_field": date_field if date_field in date_fields else "",
            "date_from": fields.Date.to_string(date_from) if date_from else "",
            "date_to": fields.Date.to_string(date_to) if date_to else "",
            "sort": sort,
            "direction": direction,
        }
        return {
            "query": query,
            "status_options": status_options,
            "service_options": service_options,
            "date_from": date_from,
            "date_to": date_to,
            "sort": sort,
            "direction": direction,
            "order": "%s %s, id %s" % (
                sort_fields[sort], direction, direction,
            ),
            "invalid": invalid,
        }

    def _portal_filter_domain(
        self, query_values, search_fields, date_fields, status_field,
    ):
        if query_values["invalid"]:
            return Domain.FALSE
        query = query_values["query"]
        domains = []
        if query["search"]:
            domains.append(Domain.OR([
                Domain(field_name, "ilike", query["search"])
                for field_name in search_fields
            ]))
        if query["status"]:
            domains.append(Domain(status_field, "=", query["status"]))
        if query["service"]:
            domains.append(Domain("service_type", "=", query["service"]))
        if query["date_field"]:
            field_name, field_type = date_fields[query["date_field"]]
            if query_values["date_from"]:
                lower = query_values["date_from"]
                if field_type == "datetime":
                    lower = datetime.combine(lower, time.min)
                domains.append(Domain(field_name, ">=", lower))
            if query_values["date_to"]:
                upper = query_values["date_to"]
                if field_type == "datetime":
                    upper = datetime.combine(upper + timedelta(days=1), time.min)
                    domains.append(Domain(field_name, "<", upper))
                else:
                    domains.append(Domain(field_name, "<=", upper))
        return Domain.AND(domains) if domains else Domain.TRUE

    def _portal_url(self, base_url, values):
        values = {
            key: value for key, value in values.items()
            if value not in (False, "", None)
        }
        return "%s?%s" % (base_url, urlencode(values)) if values else base_url

    def _portal_list_values(
        self, *, base_url, filter_keys, query_values, total, pager,
        sort_fields, date_options,
    ):
        query = query_values["query"]
        sort_urls = {}
        for sort_key in sort_fields:
            if sort_key in ("newest", "completed"):
                continue
            next_direction = (
                "desc"
                if query["sort"] == sort_key and query["direction"] == "asc"
                else "asc"
            )
            sort_urls[sort_key] = self._portal_url(base_url, {
                **query, "sort": sort_key, "direction": next_direction,
            })
        category_urls = {
            key: self._portal_url(base_url, {**query, "filterby": key})
            for key in filter_keys
        }
        clear_url = self._portal_url(base_url, {
            "filterby": query["filterby"],
            "sort": query["sort"],
            "direction": query["direction"],
        })
        return {
            "list_url": base_url,
            "list_query": query,
            "pager": pager,
            "sort_urls": sort_urls,
            "sort": query["sort"],
            "direction": query["direction"],
            "category_urls": category_urls,
            "clear_filters_url": clear_url,
            "has_active_filters": any(query[key] for key in (
                "search", "status", "service", "date_field",
                "date_from", "date_to",
            )),
            "filter_error": (
                "One or more list filters were invalid. No Orders were returned."
                if query_values["invalid"] else False
            ),
            "status_options": query_values["status_options"],
            "service_options": query_values["service_options"],
            "date_options": date_options,
            "result_total": total,
            "range_start": pager["offset"] + 1 if total else 0,
            "range_end": min(
                pager["offset"] + self._portal_order_page_size, total,
            ),
        }

    @http.route()
    def home(self, **kw):
        if request.httprequest.path == "/my":
            if self._is_trucalc_bank():
                if not request.env.user.active:
                    raise request.not_found()
                try:
                    request.env.user._trucalc_bank_identity()
                except AccessError:
                    raise request.not_found()
                return request.redirect("/my/trucalc/bank/orders")
            if self._is_trucalc_vendor():
                try:
                    request.env.user._trucalc_vendor_identity()
                except AccessError:
                    raise request.not_found()
                return request.redirect("/my/trucalc/orders")
        return super().home(**kw)

    def _bank_invoice_response(self, invoice):
        if not invoice or not invoice.pdf_data:
            raise request.not_found()
        return request.make_response(base64.b64decode(invoice.pdf_data), headers=[
            ("Content-Type", "application/pdf"),
            ("Content-Disposition", content_disposition(invoice.pdf_filename)),
            ("Cache-Control", "private, no-store"), ("X-Content-Type-Options", "nosniff"),
        ])

    @http.route("/trucalc/bank-invoices/<int:invoice_id>/download", type="http", auth="user", methods=["GET"], readonly=True)
    def internal_bank_invoice_download(self, invoice_id, **kwargs):
        invoice = request.env["trucalc.bank.invoice"].browse(invoice_id)
        try:
            invoice._authorize()
        except (AccessError, ValidationError):
            raise request.not_found()
        return self._bank_invoice_response(invoice.sudo())

    @http.route("/my/trucalc/bank/orders/<string:order_number>/invoice/<int:invoice_id>/download",
                type="http", auth="user", methods=["GET"], website=True, readonly=True)
    def portal_bank_invoice_download(self, order_number, invoice_id, **kwargs):
        order, _bank = self._bank_order(order_number)
        try:
            invoice = request.env["trucalc.bank.invoice"]._bank_invoice(order, invoice_id)
        except (AccessError, ValidationError):
            raise request.not_found()
        return self._bank_invoice_response(invoice)

    def _deliverable_response(self, deliverable):
        deliverable.ensure_one()
        attachment = request.env["ir.attachment"].sudo().search([
            ("res_model", "=", "trucalc.vendor.deliverable"),
            ("res_id", "=", deliverable.id), ("res_field", "=", "file_data"),
        ], limit=1)
        if not attachment or not deliverable.file_data:
            raise request.not_found()
        return request.make_response(base64.b64decode(deliverable.file_data), headers=[
            ("Content-Type", "application/pdf"),
            ("Content-Disposition", content_disposition(deliverable.filename)),
        ])

    def _is_trucalc_vendor(self):
        return request.env.user.has_group(
            "trucalc_orders.group_vendor_portal"
        )

    def _vendor_filter_now(self):
        return fields.Datetime.now()

    def _vendor_order_filter_domain(self, filterby):
        if filterby == "active":
            return [
                ("vendor_phase", "in", ("invitation", "assignment")),
                ("order_status", "in", ("bid_requested", "assigned", "engaged")),
            ]
        if filterby == "submitted":
            return [
                ("vendor_phase", "=", "assignment"),
                ("order_status", "in", (
                    "report_received", "reviewer_assigned", "under_review",
                )),
            ]
        if filterby == "all":
            return []

        now = self._vendor_filter_now()
        cutoff = now - timedelta(days=30)
        return [
            ("vendor_phase", "=", "assignment"),
            ("order_status", "=", "completed"),
            ("completion_at", ">=", cutoff),
            ("completion_at", "<=", now),
        ]

    @http.route(
        ["/my/trucalc/orders", "/my/trucalc/orders/page/<int:page>"],
        type="http",
        auth="user",
        website=True,
        readonly=True,
    )
    def portal_my_trucalc_orders(self, page=1, filterby="active", **kwargs):
        if not self._is_trucalc_vendor():
            raise request.not_found()

        projection_model = request.env["trucalc.vendor.order"]
        filterby = (
            filterby if filterby in self._vendor_order_filter_keys else "active"
        )
        default_sort = "due_date" if filterby == "active" else (
            "completed" if filterby == "recent_completed" else "newest"
        )
        default_direction = "asc" if filterby == "active" else "desc"
        status_options = self._selection_options(
            "trucalc.vendor.order", "vendor_status",
        )
        date_fields = {
            "invitation": ("invitation_date", "datetime"),
            "bid_due": ("response_deadline", "datetime"),
            "delivery_due": ("delivery_due_date", "date"),
        }
        query_values = self._portal_list_query(
            kwargs,
            filterby=filterby,
            sort_fields=self._vendor_sort_fields,
            default_sort=default_sort,
            default_direction=default_direction,
            status_options=status_options,
            date_fields=date_fields,
        )
        try:
            # Record rules remain authoritative. Every list option only narrows
            # the non-sudo Vendor projection before count and limited retrieval.
            domain = Domain(self._vendor_order_filter_domain(filterby)) & self._portal_filter_domain(
                query_values,
                ("order_number", "borrower", "property_address"),
                date_fields,
                "vendor_status",
            )
            projection_count = projection_model.search_count(domain)
            pager_args = {
                key: value for key, value in query_values["query"].items()
                if value
            }
            pager = portal_pager(
                url="/my/trucalc/orders",
                total=projection_count,
                page=page,
                step=self._portal_order_page_size,
                url_args=pager_args,
            )
            projections = projection_model.search(
                domain,
                order=query_values["order"],
                limit=self._portal_order_page_size,
                offset=pager["offset"],
            )
        except AccessError:
            raise request.not_found()

        values = self._prepare_portal_layout_values()
        values.update({
            "page_name": "trucalc_orders",
            "projections": projections,
            "filterby": filterby,
            "empty_message": {
                "active": "No active TruCalc Orders require current Vendor work.",
                "submitted": (
                    "No submitted TruCalc Orders are currently awaiting completion."
                ),
                "recent_completed": (
                    "No TruCalc Orders were completed in the last 30 days."
                ),
                "all": "No TruCalc Orders are currently available.",
            }[filterby],
        })
        values.update(self._portal_list_values(
            base_url="/my/trucalc/orders",
            filter_keys=self._vendor_order_filter_keys,
            query_values=query_values,
            total=projection_count,
            pager=pager,
            sort_fields=self._vendor_sort_fields,
            date_options=(
                ("invitation", "Invitation Date"),
                ("bid_due", "Bid Due Date"),
                ("delivery_due", "Delivery Due Date"),
            ),
        ))
        values.update(self._portal_column_values("vendor", page))
        return request.render("trucalc_orders.portal_my_trucalc_orders", values)

    @http.route(
        "/my/trucalc/orders/<string:order_number>",
        type="http",
        auth="user",
        website=True,
        readonly=True,
    )
    def portal_my_trucalc_order(self, order_number, **kwargs):
        if not self._is_trucalc_vendor():
            raise request.not_found()

        try:
            projection = request.env["trucalc.vendor.order"].search(
                [("order_number", "=", order_number)], limit=1
            )
        except AccessError:
            raise request.not_found()
        if not projection:
            raise request.not_found()

        values = self._vendor_order_values(
            projection, submitted=kwargs.get("deliverable_submitted") == "1",
        )
        return request.render("trucalc_orders.portal_my_trucalc_order", values)

    def _vendor_order_values(self, projection, response_error=False,
                             deliverable_error=False, submitted=False):
        authorization = request.env["trucalc.order.vendor.authorization"].sudo().browse(
            projection.id
        )
        documents = request.env["trucalc.document"]._vendor_authorized_documents(
            authorization, request.env.user
        )
        deliverables = request.env[
            "trucalc.vendor.deliverable"
        ]._vendor_deliverables(authorization, request.env.user)
        valuation = deliverables.filtered(
            lambda item: item.artifact_type == "valuation" and item.is_current
        )[:1]
        vendor_invoice = deliverables.filtered(
            lambda item: item.artifact_type == "vendor_invoice"
        )[:1]
        revision_request = request.env[
            "trucalc.order.lifecycle.event"
        ]._open_valuation_revision_request(valuation) if valuation else request.env[
            "trucalc.order.lifecycle.event"
        ].browse()
        accepted_assignment = bool(
            projection.vendor_phase == "assignment"
            and projection.engagement_response_state == "accepted"
        )
        values = self._prepare_portal_layout_values()
        values.update({
            "page_name": "trucalc_order",
            "projection": projection,
            "documents": documents,
            "valuation": valuation,
            "vendor_invoice": vendor_invoice,
            "valuation_revision_request": revision_request,
            "can_submit_valuation": bool(
                accepted_assignment and (
                    (projection.order_status == "engaged" and not valuation)
                    or (
                        valuation and revision_request
                        and projection.order_status in (
                            "report_received", "reviewer_assigned", "under_review",
                        )
                    )
                )
            ),
            "can_submit_vendor_invoice": bool(
                accepted_assignment
                and projection.order_status in (
                    "engaged", "report_received", "reviewer_assigned", "under_review",
                )
                and not vendor_invoice
            ),
            "response_error": response_error,
            "deliverable_error": deliverable_error,
            "deliverable_submitted": submitted,
        })
        return values

    @http.route(
        "/my/trucalc/orders/<string:order_number>/documents/<int:document_id>/download",
        type="http", auth="user", website=True, readonly=True,
    )
    def portal_trucalc_document_download(self, order_number, document_id, **kwargs):
        projection = self._vendor_projection(order_number)
        authorization = request.env["trucalc.order.vendor.authorization"].sudo().browse(
            projection.id
        )
        documents = request.env["trucalc.document"]._vendor_authorized_documents(
            authorization, request.env.user
        )
        document = documents.filtered(lambda item: item.id == document_id)
        if len(document) != 1 or not document.attachment:
            raise request.not_found()
        return self._document_response(document)

    def _document_response(self, document):
        document.ensure_one()
        attachment = request.env["ir.attachment"].sudo().search([
            ("res_model", "=", "trucalc.document"),
            ("res_id", "=", document.id), ("res_field", "=", "attachment"),
        ], limit=1)
        if not attachment:
            raise request.not_found()
        return request.make_response(base64.b64decode(document.attachment), headers=[
            ("Content-Type", attachment.mimetype or "application/octet-stream"),
            ("Content-Disposition", content_disposition(document.filename)),
        ])

    def _is_trucalc_bank(self):
        return request.env.user._trucalc_has_bank_role()

    def _bank_filter_now(self):
        return fields.Datetime.now()

    def _bank_order_filter_domain(self, filterby, bank):
        if filterby == "open":
            return [("status", "not in", ("cancelled", "declined", "completed"))]
        if filterby == "all":
            return [("status", "!=", "draft")]
        if filterby == "recent_completed":
            now = self._bank_filter_now()
            cutoff = now - timedelta(days=30)
            events = request.env["trucalc.order.lifecycle.event"].sudo().search([
                ("company_id", "=", bank.id),
                ("event_type", "=", "order_completed"),
                ("event_at", ">=", cutoff),
                ("event_at", "<=", now),
            ])
            return [
                ("status", "=", "completed"),
                ("id", "in", events.mapped("order_id").ids),
            ]
        invoices = request.env["trucalc.bank.invoice"].sudo().search([
            ("company_id", "=", bank.id),
            ("order_id.company_id", "=", bank.id),
            ("order_id.status", "=", "completed"),
            ("status", "=", "issued"),
        ])
        return [
            ("status", "=", "completed"),
            ("id", "in", invoices.mapped("order_id").ids),
        ]

    def _bank_order(self, order_number):
        if not self._is_trucalc_bank():
            raise request.not_found()
        try:
            bank = request.env.user._trucalc_bank_identity()
        except AccessError:
            raise request.not_found()
        order = request.env["trucalc.order"].sudo().search([
            ("order_number", "=", order_number), ("company_id", "=", bank.id),
        ], limit=1)
        if not order:
            raise request.not_found()
        if order.status == "draft":
            is_admin = request.env.user.has_group(
                "trucalc_orders.group_bank_admin"
            )
            is_creator = (
                request.env.user.has_group(
                    "trucalc_orders.group_bank_requestor"
                )
                and order.requestor_id == request.env.user
            )
            if not (is_admin or is_creator):
                raise request.not_found()
        return order, bank

    @http.route(
        "/my/trucalc/bank/orders/<string:order_number>/fee/<int:fee_request_id>/<string:decision>",
        type="http", auth="user", website=True, methods=["POST"], csrf=True,
    )
    def portal_bank_fee_decision(self, order_number, fee_request_id, decision, **post):
        order, _bank = self._bank_order(order_number)
        fee_request = request.env["trucalc.fee.change.request"].browse(fee_request_id)
        if set(post) - {"csrf_token", "decline_reason"}:
            raise request.not_found()
        try:
            fee_request._decide(order, decision, post.get("decline_reason"))
        except AccessError:
            raise request.not_found()
        except ValidationError as error:
            return self.portal_bank_orders(fee_error=error.args[0])
        return request.redirect("/my/trucalc/bank/orders")

    def _bank_approved_valuation(self, order):
        """Bank release boundary; future controlled closeout owns completion."""
        if order.status != "completed":
            return (
                request.env["trucalc.vendor.deliverable"].browse(),
                request.env["trucalc.order.lifecycle.event"].browse(),
            )
        valuation = request.env["trucalc.vendor.deliverable"].sudo().search([
            ("order_id", "=", order.id),
            ("artifact_type", "=", "valuation"),
            ("is_current", "=", True),
            ("status", "=", "submitted"),
        ], limit=1)
        if not valuation:
            return valuation, request.env["trucalc.order.lifecycle.event"].browse()
        approval = request.env[
            "trucalc.order.lifecycle.event"
        ]._valuation_approval(valuation)
        return (valuation, approval) if approval else (
            request.env["trucalc.vendor.deliverable"].browse(), approval
        )

    def _can_create_bank_draft(self):
        return (
            self._is_trucalc_bank()
            and (
                request.env.user.has_group("trucalc_orders.group_bank_admin")
                or request.env.user.has_group("trucalc_orders.group_bank_requestor")
            )
        )

    def _bank_request_form_values(
        self, order=False, form_values=None, submission_error=False,
    ):
        service_areas = request.env["trucalc.service.area"].sudo().search([
            ("active", "=", True),
        ])
        if form_values is None and order:
            area = order.service_area_id
            form_values = {
                "borrower": order.borrower or "",
                "property_address": order.property_address or "",
                "city": order.city or "",
                "zip_code": order.zip_code or "",
                "loan_number": order.loan_number or "",
                "loan_purpose": order.loan_purpose or "",
                "service_type": order.service_type or "",
                "property_type": order.property_type or "",
                "due_date": fields.Date.to_string(order.due_date) if order.due_date else "",
                "inspection_contact_name": order.inspection_contact_name or "",
                "inspection_contact_phone": order.inspection_contact_phone_display or "",
                "inspection_contact_email": order.inspection_contact_email or "",
                "notes": order.notes or "",
                "service_area_id": str(area.id) if area else "",
                "service_state_id": str(area.state_id.id) if area else "",
                "service_county": area.county if area else "",
            }
        elif form_values is not None:
            form_values = dict(form_values)
            if "inspection_contact_phone" in form_values:
                form_values["inspection_contact_phone"] = request.env[
                    "trucalc.order"
                ]._format_inspection_contact_phone(
                    form_values["inspection_contact_phone"]
                ) or ""
        elif not order:
            form_values = {"property_type": "single_family"}
        bank = request.env.user._trucalc_bank_identity()
        matrix = []
        for area in service_areas:
            try:
                pricing = request.env["trucalc.order"]._resolve_bank_fee(bank, area)
                service_fee = "%.2f" % pricing["agreed_fee"]
            except ValidationError:
                service_fee = False
            matrix.append({
                "id": area.id,
                "state_id": area.state_id.id,
                "state_name": area.state_id.name,
                "county": area.county,
                "service_type": area.service_type,
                "service_label": dict(area._fields["service_type"].selection).get(
                    area.service_type, area.service_type,
                ),
                "service_fee": service_fee,
            })
        values = self._prepare_portal_layout_values()
        values.update({
            "page_name": "trucalc_bank_order_draft" if order else "trucalc_bank_order_new",
            "order": order,
            "service_areas": service_areas,
            "service_area_matrix": json.dumps(matrix),
            "property_types": request.env[
                "trucalc.order"
            ]._bank_request_property_types(),
            "loan_purposes": request.env[
                "trucalc.order"
            ]._fields["loan_purpose"].selection,
            "form_values": form_values or {},
            "submission_error": submission_error,
            "filterby": False,
            "today": fields.Date.to_string(fields.Date.context_today(
                request.env["trucalc.order"]
            )),
        })
        return values

    @http.route(
        "/my/trucalc/bank/orders/new",
        type="http", auth="user", website=True, methods=["GET"], readonly=True,
    )
    def portal_bank_order_new(self, **kwargs):
        if not self._can_create_bank_draft():
            raise request.not_found()
        try:
            request.env.user._trucalc_bank_identity()
        except AccessError:
            raise request.not_found()
        return request.render(
            "trucalc_orders.portal_bank_order_new",
            self._bank_request_form_values(),
        )

    @http.route(
        "/my/trucalc/bank/orders/new",
        type="http", auth="user", website=True, methods=["POST"],
    )
    def portal_bank_order_submit(self, **post):
        if not self._can_create_bank_draft():
            raise request.not_found()
        submitted = dict(post)
        submitted.pop("csrf_token", None)
        try:
            order = request.env["trucalc.order"]._create_bank_draft(
                submitted, request.env.user,
            )
        except (AccessError, ValidationError, ValueError) as error:
            return request.render(
                "trucalc_orders.portal_bank_order_new",
                self._bank_request_form_values(
                    form_values=submitted, submission_error=error.args[0]
                ),
            )
        return request.redirect(
            "/my/trucalc/bank/orders/%s/documents?created=1"
            % order.order_number
        )

    @http.route(
        "/my/trucalc/bank/orders/<string:order_number>/draft/save",
        type="http", auth="user", website=True, methods=["POST"],
    )
    def portal_bank_draft_save(self, order_number, **post):
        order, _bank = self._bank_order(order_number)
        submitted = dict(post)
        submitted.pop("csrf_token", None)
        if submitted.pop("draft_action", None) != "save":
            raise request.not_found()
        try:
            request.env["trucalc.order"]._update_bank_draft(
                order, submitted, request.env.user,
            )
        except (AccessError, ValidationError, ValueError) as error:
            return self.portal_bank_documents(
                order_number, draft_error=error.args[0], form_values=submitted,
            )
        return request.redirect(
            "/my/trucalc/bank/orders/%s/documents?saved=1" % order_number
        )

    @http.route(
        "/my/trucalc/bank/orders/<string:order_number>/draft/send",
        type="http", auth="user", website=True, methods=["POST"],
    )
    def portal_bank_draft_send(self, order_number, **post):
        order, _bank = self._bank_order(order_number)
        submitted = dict(post)
        submitted.pop("csrf_token", None)
        if submitted.pop("draft_action", None) != "send":
            raise request.not_found()
        try:
            request.env["trucalc.order"]._send_bank_draft(
                order, submitted, request.env.user,
            )
        except (AccessError, ValidationError, ValueError) as error:
            return self.portal_bank_documents(
                order_number, draft_error=error.args[0], form_values=submitted,
            )
        return request.redirect(
            "/my/trucalc/bank/orders/%s/documents?submitted=1" % order_number
        )

    @http.route(
        ["/my/trucalc/bank/orders", "/my/trucalc/bank/orders/page/<int:page>"],
        type="http", auth="user", website=True, readonly=True,
    )
    def portal_bank_orders(self, page=1, fee_error=False, filterby="open", **kwargs):
        if not self._is_trucalc_bank():
            raise request.not_found()
        bank = request.env.user._trucalc_bank_identity()
        filterby = filterby if filterby in self._bank_order_filter_keys else "open"
        model = request.env["trucalc.order"].sudo()
        date_fields = {"request": ("order_date", "date")}
        query_values = self._portal_list_query(
            kwargs,
            filterby=filterby,
            sort_fields=self._bank_sort_fields,
            default_sort="newest",
            default_direction="desc",
            status_options=self._selection_options("trucalc.order", "status"),
            date_fields=date_fields,
        )
        # Authorization is established first; filters only narrow this domain.
        domain = Domain("company_id", "=", bank.id)
        if request.env.user.has_group("trucalc_orders.group_bank_view_only"):
            domain &= Domain("status", "!=", "draft")
        elif request.env.user.has_group("trucalc_orders.group_bank_requestor"):
            domain &= Domain.OR([
                Domain("status", "!=", "draft"),
                Domain("requestor_id", "=", request.env.user.id),
            ])
        domain &= Domain(self._bank_order_filter_domain(filterby, bank))
        domain &= self._portal_filter_domain(
            query_values,
            ("order_number", "borrower", "property_address"),
            date_fields,
            "status",
        )
        order_count = model.search_count(domain)
        pager_args = {
            key: value for key, value in query_values["query"].items()
            if value
        }
        pager = portal_pager(
            url="/my/trucalc/bank/orders", total=order_count,
            page=page, step=self._portal_order_page_size,
            url_args=pager_args,
        )
        orders = model.search(
            domain,
            order=query_values["order"],
            limit=self._portal_order_page_size,
            offset=pager["offset"],
        )
        column_values = self._portal_column_values("bank", page)
        completion_dates = {}
        if orders and "completion_date" in column_values["portal_columns"]:
            completion_events = request.env[
                "trucalc.order.lifecycle.event"
            ].sudo().search([
                ("order_id", "in", orders.ids),
                ("company_id", "=", bank.id),
                ("event_type", "=", "order_completed"),
            ])
            completion_dates = {
                event.order_id.id: event.event_at for event in completion_events
            }
        values = self._prepare_portal_layout_values()
        values.update({
            "page_name": "trucalc_bank_orders",
            "orders": orders,
            "can_submit": self._can_create_bank_draft(),
            "filterby": filterby,
            "completion_dates": completion_dates,
            "empty_message": {
                "open": "No open TruCalc requests.",
                "recent_completed": "No requests were completed in the last 30 days.",
                "outstanding_invoices": "No outstanding invoices.",
                "all": "No TruCalc requests found.",
            }[filterby],
        })
        # Presentation-only projection, restricted to the authorized page of Orders.
        pending = request.env["trucalc.fee.change.request"].sudo().search([
            ("order_id", "in", values["orders"].ids),
            ("company_id", "=", bank.id), ("state", "=", "pending"),
        ])
        values.update({
            "pending_fees": {item.order_id.id: {
                "id": item.id, "current_fee": item.order_id.current_agreed_fee,
                "requested_fee": item.proposed_fee, "reason": item.reason,
                "requested_at": item.requested_at,
                "location_correction": (
                    item.location_correction_request_id._portal_snapshot()
                    if item.location_correction_request_id else False
                ),
            } for item in pending},
            "can_decide_fee": request.env[
                "trucalc.fee.change.request"
            ]._can_decide(),
            "fee_error": fee_error,
        })
        values.update(self._portal_list_values(
            base_url="/my/trucalc/bank/orders",
            filter_keys=self._bank_order_filter_keys,
            query_values=query_values,
            total=order_count,
            pager=pager,
            sort_fields=self._bank_sort_fields,
            date_options=(("request", "Request Date"),),
        ))
        values.update(column_values)
        return request.render("trucalc_orders.portal_bank_orders", values)

    @http.route(
        "/my/trucalc/bank/orders/<string:order_number>/documents",
        type="http", auth="user", website=True, readonly=True,
    )
    def portal_bank_documents(
        self, order_number, upload_error=False, submitted=False, created=False,
        saved=False, draft_error=False, form_values=None, **kwargs
    ):
        order, bank = self._bank_order(order_number)
        documents = request.env["trucalc.document"].sudo().search([
            ("order_id", "=", order.id), ("active", "=", True),
            ("origin", "=", "bank"), ("originating_bank_id", "=", bank.id),
        ])
        tags = request.env["trucalc.document.tag"].sudo().search([
            ("active", "=", True),
        ])
        can_upload = (
            request.env.user.has_group("trucalc_orders.group_bank_admin")
            or request.env.user.has_group("trucalc_orders.group_bank_requestor")
        )
        values = self._bank_request_form_values(
            order=order if order.status == "draft" else False,
            form_values=form_values,
            submission_error=draft_error,
        )
        values.update({
            "page_name": "trucalc_bank_documents", "order": order,
            "documents": documents, "tags": tags, "can_upload": can_upload,
            "upload_error": upload_error,
            "fee_requests": request.env["trucalc.fee.change.request"]._portal_values(order),
            "submitted": submitted,
            "created": created,
            "saved": saved,
            "can_delete_draft_documents": order.status == "draft",
        })
        approved_valuation, valuation_approval = self._bank_approved_valuation(order)
        values.update({
            "approved_valuation": approved_valuation,
            "valuation_approval": valuation_approval,
            "bank_invoice": request.env["trucalc.bank.invoice"]._portal_values(order),
        })
        return request.render("trucalc_orders.portal_bank_documents", values)

    @http.route(
        "/my/trucalc/bank/orders/<string:order_number>/valuation/"
        "<int:deliverable_id>/download",
        type="http", auth="user", website=True, readonly=True,
    )
    def portal_bank_approved_valuation_download(
        self, order_number, deliverable_id, **kwargs
    ):
        order, _bank = self._bank_order(order_number)
        valuation, approval = self._bank_approved_valuation(order)
        if (
            not approval or len(valuation) != 1
            or valuation.id != deliverable_id
            or valuation.order_id != order
            or valuation.artifact_type != "valuation"
            or not valuation.is_current
        ):
            raise request.not_found()
        return self._deliverable_response(valuation)

    @http.route(
        "/my/trucalc/bank/orders/<string:order_number>/documents/upload",
        type="http", auth="user", website=True, methods=["POST"],
    )
    def portal_bank_document_upload(self, order_number, **post):
        order, _bank = self._bank_order(order_number)
        if order.status == "completed":
            raise request.not_found()
        upload = request.httprequest.files.get("document_file")
        draft_values = dict(post)
        draft_values.pop("csrf_token", None)
        draft_values.pop("tag_id", None)
        draft_values.pop("document_file", None)
        if draft_values.pop("draft_action", None) != "upload":
            raise request.not_found()
        try:
            with request.env.cr.savepoint():
                tag = request.env["trucalc.document.tag"].sudo().browse(
                    int(post.get("tag_id", 0))
                ).exists()
                if not upload or not tag:
                    raise ValidationError("Select a file and an active Document Tag.")
                if order.status == "draft" and draft_values:
                    request.env["trucalc.order"]._update_bank_draft(
                        order, draft_values, request.env.user,
                    )
                payload = upload.stream.read(MAX_DOCUMENT_BYTES + 1)
                request.env["trucalc.document"]._create_bank_document(
                    order, tag, upload.filename,
                    base64.b64encode(payload), request.env.user,
                )
        except (AccessError, ValidationError, ValueError) as error:
            return self.portal_bank_documents(
                order_number, upload_error=error.args[0],
                form_values=draft_values if order.status == "draft" else None,
            )
        return request.redirect(
            "/my/trucalc/bank/orders/%s/documents" % order_number
        )

    @http.route(
        "/my/trucalc/bank/orders/<string:order_number>/documents/"
        "<int:document_id>/delete",
        type="http", auth="user", website=True, methods=["POST"],
    )
    def portal_bank_draft_document_delete(
        self, order_number, document_id, **post
    ):
        order, _bank = self._bank_order(order_number)
        document = request.env["trucalc.document"].sudo().browse(
            document_id
        )
        try:
            request.env["trucalc.document"]._delete_bank_draft_document(
                document, order, request.env.user,
            )
        except (AccessError, ValidationError):
            raise request.not_found()
        return request.redirect(
            "/my/trucalc/bank/orders/%s/documents" % order_number
        )

    @http.route(
        "/my/trucalc/bank/orders/<string:order_number>/documents/<int:document_id>/download",
        type="http", auth="user", website=True, readonly=True,
    )
    def portal_bank_document_download(self, order_number, document_id, **kwargs):
        order, bank = self._bank_order(order_number)
        document = request.env["trucalc.document"].sudo().search([
            ("id", "=", document_id), ("order_id", "=", order.id),
            ("active", "=", True), ("origin", "=", "bank"),
            ("originating_bank_id", "=", bank.id),
        ], limit=1)
        if not document or not document.attachment:
            raise request.not_found()
        return self._document_response(document)

    def _vendor_projection(self, order_number):
        if not self._is_trucalc_vendor():
            raise request.not_found()
        try:
            projection = request.env["trucalc.vendor.order"].search(
                [("order_number", "=", order_number)], limit=1
            )
        except AccessError:
            raise request.not_found()
        if not projection:
            raise request.not_found()
        return projection

    def _render_response_error(self, projection, message):
        values = self._vendor_order_values(projection, response_error=message)
        return request.render("trucalc_orders.portal_my_trucalc_order", values)

    def _render_deliverable_error(self, projection, message):
        values = self._vendor_order_values(projection, deliverable_error=message)
        return request.render("trucalc_orders.portal_my_trucalc_order", values)

    def _uploaded_pdf(self, field_name):
        uploaded = request.httprequest.files.get(field_name)
        if not uploaded or not uploaded.filename:
            raise ValidationError("Select a PDF file to submit.")
        payload = uploaded.stream.read(50 * 1024 * 1024 + 1)
        return uploaded.filename, base64.b64encode(payload)

    def _submit_deliverable(self, order_number, artifact_type, field_name):
        projection = self._vendor_projection(order_number)
        authorization = request.env["trucalc.order.vendor.authorization"].sudo().browse(
            projection.id
        )
        try:
            filename, file_data = self._uploaded_pdf(field_name)
            request.env["trucalc.vendor.deliverable"]._submit(
                authorization, request.env.user, artifact_type, filename, file_data,
            )
        except (AccessError, ValidationError) as error:
            return self._render_deliverable_error(projection, error.args[0])
        return request.redirect(
            "/my/trucalc/orders/%s?deliverable_submitted=1" % order_number
        )

    @http.route(
        "/my/trucalc/orders/<string:order_number>/deliverables/valuation",
        type="http", auth="user", website=True, methods=["POST"],
    )
    def portal_trucalc_valuation_submit(self, order_number, **post):
        return self._submit_deliverable(
            order_number, "valuation", "valuation_file",
        )

    @http.route(
        "/my/trucalc/orders/<string:order_number>/deliverables/vendor-invoice",
        type="http", auth="user", website=True, methods=["POST"],
    )
    def portal_trucalc_vendor_invoice_submit(self, order_number, **post):
        return self._submit_deliverable(
            order_number, "vendor_invoice", "vendor_invoice_file",
        )

    @http.route(
        "/my/trucalc/orders/<string:order_number>/deliverables/"
        "<int:deliverable_id>/download",
        type="http", auth="user", website=True, readonly=True,
    )
    def portal_trucalc_deliverable_download(
        self, order_number, deliverable_id, **kwargs
    ):
        projection = self._vendor_projection(order_number)
        deliverable = request.env["trucalc.vendor.deliverable"].browse(
            deliverable_id
        )
        try:
            deliverable = deliverable._authorize_download(request.env.user)
        except AccessError:
            raise request.not_found()
        authorization = request.env["trucalc.order.vendor.authorization"].sudo().browse(
            projection.id
        )
        if deliverable.authorization_id != authorization:
            raise request.not_found()
        return self._deliverable_response(deliverable)

    @http.route(
        "/trucalc/deliverables/<int:deliverable_id>/download",
        type="http", auth="user", readonly=True,
    )
    def trucalc_internal_deliverable_download(self, deliverable_id, **kwargs):
        deliverable = request.env["trucalc.vendor.deliverable"].browse(
            deliverable_id
        )
        try:
            deliverable = deliverable._authorize_download(request.env.user)
        except AccessError:
            raise request.not_found()
        return self._deliverable_response(deliverable)

    @http.route(
        "/my/trucalc/orders/<string:order_number>/response",
        type="http", auth="user", website=True, methods=["POST"],
    )
    def portal_trucalc_order_response(self, order_number, **post):
        projection = self._vendor_projection(order_number)
        try:
            projection.action_vendor_response(
                post.get("response_type"), proposed_fee=post.get("proposed_fee"),
                proposed_delivery_date=post.get("proposed_delivery_date"),
                comments=post.get("comments"),
            )
        except (AccessError, ValidationError) as error:
            return self._render_response_error(projection, error.args[0])
        return request.redirect("/my/trucalc/orders/%s" % order_number)

    @http.route(
        "/my/trucalc/orders/<string:order_number>/decline",
        type="http", auth="user", website=True, methods=["POST"],
    )
    def portal_trucalc_order_decline(self, order_number, **post):
        projection = self._vendor_projection(order_number)
        try:
            projection.action_vendor_decline(post.get("decline_reason"))
        except (AccessError, ValidationError) as error:
            return self._render_response_error(projection, error.args[0])
        return request.redirect("/my/trucalc/orders/%s" % order_number)

    @http.route(
        "/my/trucalc/orders/<string:order_number>/engagement/accept",
        type="http", auth="user", website=True, methods=["POST"],
    )
    def portal_trucalc_engagement_accept(self, order_number, **post):
        projection = self._vendor_projection(order_number)
        try:
            projection.action_vendor_accept_engagement()
        except (AccessError, ValidationError) as error:
            return self._render_response_error(projection, error.args[0])
        return request.redirect("/my/trucalc/orders/%s" % order_number)

    @http.route(
        "/my/trucalc/orders/<string:order_number>/engagement/request-delivery-change",
        type="http", auth="user", website=True, methods=["POST"],
    )
    def portal_trucalc_engagement_request_delivery_change(
        self, order_number, **post
    ):
        projection = self._vendor_projection(order_number)
        try:
            projection.action_vendor_request_delivery_change(
                post.get("requested_delivery_date"), post.get("reason")
            )
        except (AccessError, ValidationError) as error:
            return self._render_response_error(projection, error.args[0])
        return request.redirect("/my/trucalc/orders/%s" % order_number)

    @http.route(
        "/my/trucalc/orders/<string:order_number>/engagement/decline",
        type="http", auth="user", website=True, methods=["POST"],
    )
    def portal_trucalc_engagement_decline(self, order_number, **post):
        projection = self._vendor_projection(order_number)
        try:
            projection.action_vendor_decline_engagement(post.get("decline_reason"))
        except (AccessError, ValidationError) as error:
            return self._render_response_error(projection, error.args[0])
        return request.redirect("/my/trucalc/orders/%s" % order_number)
