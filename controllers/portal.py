from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request

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

        values = self._prepare_portal_layout_values()
        values.update({
            "page_name": "trucalc_order",
            "projection": projection,
        })
        return request.render("trucalc_orders.portal_my_trucalc_order", values)
