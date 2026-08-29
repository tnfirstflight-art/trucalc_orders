import base64

from odoo import http
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
        return order, bank

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
        })
        return request.render("trucalc_orders.portal_bank_orders", values)

    @http.route(
        "/my/trucalc/bank/orders/<string:order_number>/documents",
        type="http", auth="user", website=True, readonly=True,
    )
    def portal_bank_documents(self, order_number, upload_error=False, **kwargs):
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
        values = self._prepare_portal_layout_values()
        values.update({
            "page_name": "trucalc_bank_documents", "order": order,
            "documents": documents, "tags": tags, "can_upload": can_upload,
            "upload_error": upload_error,
        })
        return request.render("trucalc_orders.portal_bank_documents", values)

    @http.route(
        "/my/trucalc/bank/orders/<string:order_number>/documents/upload",
        type="http", auth="user", website=True, methods=["POST"],
    )
    def portal_bank_document_upload(self, order_number, **post):
        order, _bank = self._bank_order(order_number)
        upload = request.httprequest.files.get("document_file")
        try:
            tag = request.env["trucalc.document.tag"].sudo().browse(
                int(post.get("tag_id", 0))
            ).exists()
            if not upload or not tag:
                raise ValidationError("Select a file and an active Document Tag.")
            request.env["trucalc.document"]._create_bank_document(
                order, tag, upload.filename,
                base64.b64encode(upload.read()), request.env.user,
            )
        except (AccessError, ValidationError, ValueError) as error:
            return self.portal_bank_documents(order_number, upload_error=error.args[0])
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
