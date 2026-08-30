import base64
import json

from odoo import fields, http
from odoo.exceptions import AccessError, ValidationError
from odoo.http import request
from odoo.http import content_disposition

from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager


class TruCalcVendorPortal(CustomerPortal):
    def _is_trucalc_vendor(self):
        return request.env.user.has_group(
            "trucalc_orders.group_vendor_portal"
        )

    @http.route(
        ["/my/trucalc/orders", "/my/trucalc/orders/page/<int:page>"],
        type="http",
        auth="user",
        website=True,
        readonly=True,
    )
    def portal_my_trucalc_orders(self, page=1, **kwargs):
        if not self._is_trucalc_vendor():
            raise request.not_found()

        projection_model = request.env["trucalc.vendor.order"]
        try:
            projection_count = projection_model.search_count([])
            pager = portal_pager(
                url="/my/trucalc/orders",
                total=projection_count,
                page=page,
                step=self._items_per_page,
            )
            projections = projection_model.search(
                [],
                order="order_number, id",
                limit=self._items_per_page,
                offset=pager["offset"],
            )
        except AccessError:
            raise request.not_found()

        values = self._prepare_portal_layout_values()
        values.update({
            "page_name": "trucalc_orders",
            "projections": projections,
            "pager": pager,
        })
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

        authorization = request.env["trucalc.order.vendor.authorization"].sudo().browse(
            projection.id
        )
        documents = request.env["trucalc.document"]._vendor_authorized_documents(
            authorization, request.env.user
        )

        values = self._prepare_portal_layout_values()
        values.update({
            "page_name": "trucalc_order",
            "projection": projection,
            "documents": documents,
            "response_error": False,
        })
        return request.render("trucalc_orders.portal_my_trucalc_order", values)

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
        matrix = [{
            "id": area.id,
            "state_id": area.state_id.id,
            "state_name": area.state_id.name,
            "county": area.county,
            "service_type": area.service_type,
            "service_label": dict(area._fields["service_type"].selection).get(
                area.service_type, area.service_type,
            ),
        } for area in service_areas]
        values = self._prepare_portal_layout_values()
        values.update({
            "page_name": "trucalc_bank_order_draft" if order else "trucalc_bank_order_new",
            "order": order,
            "service_areas": service_areas,
            "service_area_matrix": json.dumps(matrix),
            "property_types": request.env["trucalc.order"]._fields[
                "property_type"
            ].selection,
            "form_values": form_values or {},
            "submission_error": submission_error,
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
    def portal_bank_orders(self, page=1, **kwargs):
        if not self._is_trucalc_bank():
            raise request.not_found()
        bank = request.env.user._trucalc_bank_identity()
        model = request.env["trucalc.order"].sudo()
        domain = [("company_id", "=", bank.id)]
        if request.env.user.has_group("trucalc_orders.group_bank_view_only"):
            domain.append(("status", "!=", "draft"))
        elif request.env.user.has_group("trucalc_orders.group_bank_requestor"):
            domain += [
                "|", ("status", "!=", "draft"),
                ("requestor_id", "=", request.env.user.id),
            ]
        pager = portal_pager(
            url="/my/trucalc/bank/orders", total=model.search_count(domain),
            page=page, step=self._items_per_page,
        )
        values = self._prepare_portal_layout_values()
        values.update({
            "page_name": "trucalc_bank_orders",
            "orders": model.search(domain, order="order_number desc",
                                   limit=self._items_per_page, offset=pager["offset"]),
            "pager": pager,
            "can_submit": self._can_create_bank_draft(),
        })
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
            "submitted": submitted,
            "created": created,
            "saved": saved,
            "can_delete_draft_documents": order.status == "draft",
        })
        return request.render("trucalc_orders.portal_bank_documents", values)

    @http.route(
        "/my/trucalc/bank/orders/<string:order_number>/documents/upload",
        type="http", auth="user", website=True, methods=["POST"],
    )
    def portal_bank_document_upload(self, order_number, **post):
        order, _bank = self._bank_order(order_number)
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
                request.env["trucalc.document"]._create_bank_document(
                    order, tag, upload.filename,
                    base64.b64encode(upload.read()), request.env.user,
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
        values = self._prepare_portal_layout_values()
        values.update({"page_name": "trucalc_order", "projection": projection,
                       "response_error": message})
        return request.render("trucalc_orders.portal_my_trucalc_order", values)

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
