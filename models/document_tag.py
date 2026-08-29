from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


class TruCalcDocumentTag(models.Model):
    _name = "trucalc.document.tag"
    _description = "TruCalc Document Tag"
    _order = "sequence, name, id"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)

    _name_unique = models.UniqueIndex(
        "(lower(name))", "Document Tag names must be unique (case-insensitive)."
    )

    @api.constrains("name")
    def _check_name(self):
        for tag in self:
            if not tag.name or not tag.name.strip():
                raise ValidationError(_("Document Tag name is required."))

    def _require_admin(self):
        if not self.env.su and not self.env.user.has_group(
            "trucalc_orders.group_trucalc_admin"
        ):
            raise AccessError(_("Only TruCalc Administrators may maintain Document Tags."))

    @api.model_create_multi
    def create(self, vals_list):
        self._require_admin()
        return super().create(vals_list)

    def write(self, vals):
        self._require_admin()
        return super().write(vals)

    def unlink(self):
        self._require_admin()
        if self.env["trucalc.document"].with_context(active_test=False).sudo().search_count([
            ("tag_id", "in", self.ids),
        ]):
            raise ValidationError(_("A Document Tag used by a document cannot be deleted."))
        return super().unlink()
