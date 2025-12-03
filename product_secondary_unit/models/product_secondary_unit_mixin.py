# Copyright 2021 Tecnativa - Sergio Teruel
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import api, fields, models
from odoo.tools.float_utils import float_round


class ProductSecondaryUnitMixin(models.AbstractModel):
    """
    Mixin model that allows to compute a field from a secondary unit helper
    An example is to extend any model in which you want to compute quantities
    based on secondary units. You must add a dictionary `_secondary_unit_fields`
    as class variable with the following content:
    _secondary_unit_fields = {
        "qty_field": "product_uom_qty",
        "uom_field": "product_uom"
    }

    To compute ``qty_field`` on target model, you must convert the field to computed
    writable (computed, stored and readonly=False), and you have to define the
    compute method adding ``secondary_uom_id`` and ``secondary_uom_qty`` fields
    as dependencies and calling inside to ``self._compute_helper_target_field_qty()``.

    To compute secondary units when user changes the uom field on target model,
    you must add an onchange method on uom field and call to
    ``self._onchange_helper_product_uom_for_secondary()``

    You can see an example in ``purchase_order_secondary_unit`` on purchase-workflow
    repository.
    """

    _name = "product.secondary.unit.mixin"
    _description = "Product Secondary Unit Mixin"
    _secondary_unit_fields = {}
    _product_uom_field = "uom_id"

    @api.model
    def _get_default_secondary_uom(self):
        return self.env["product.template"]._get_default_secondary_uom()

    secondary_uom_qty = fields.Float(
        string="Secondary Qty",
        digits="Product Unit of Measure",
        store=True,
        readonly=False,
        compute="_compute_secondary_uom_qty",
        precompute=True,
    )
    secondary_uom_id = fields.Many2one(
        comodel_name="product.secondary.unit",
        string="Second unit",
        ondelete="restrict",
        default=_get_default_secondary_uom,
    )

    def _get_uom_line(self):
        return self[self._secondary_unit_fields["uom_field"]]

    def _get_factor_line(self):
        """
        Calculate the conversion factor from the line UOM to the secondary UOM.
        This method properly handles units of measure within the same category
        by using Odoo's built-in conversion methods.
        
        The secondary unit factor is always relative to the product's base UOM.
        When the line UOM differs from the product UOM, we need to:
        1. If line UOM and secondary UOM are in the same category, convert directly
        2. Otherwise, convert from line UOM to product UOM, then apply secondary factor
        """
        uom_line = self._get_uom_line()
        product_uom = self.product_id[self._product_uom_field]
        secondary_uom = self.secondary_uom_id.uom_id
        
        # If line UOM is the same as product UOM, use the secondary unit factor directly
        if product_uom == uom_line:
            return self.secondary_uom_id.factor
        
        # Check if line UOM and secondary UOM are in the same category
        same_category_line_secondary = uom_line.category_id == secondary_uom.category_id
        
        if same_category_line_secondary:
            # Line UOM and secondary UOM are in the same category
            # Convert directly from line UOM to secondary UOM
            # This gives us how many secondary UOM units are in 1 line UOM unit
            # Example: 1 Maple (30 huevos) -> Huevo = 30
            qty_in_secondary_uom = uom_line._compute_quantity(
                qty=1.0,
                to_unit=secondary_uom,
                round=False,
            )
            # The factor is used in division: secondary_uom_qty = qty_line / factor
            # So if 1 line UOM = qty_in_secondary_uom * secondary UOM,
            # then factor = 1 / qty_in_secondary_uom
            # Example: 1 Maple = 30 Huevos, so factor = 1/30
            # Then: secondary_uom_qty = 1 Maple / (1/30) = 30 Huevos ✓
            factor = 1.0 / qty_in_secondary_uom if qty_in_secondary_uom else 1.0
        else:
            # Line UOM and secondary UOM are in different categories
            # Check if line UOM and product UOM are in the same category
            same_category_line_product = uom_line.category_id == product_uom.category_id
            
            if same_category_line_product:
                # Convert from line UOM to product UOM, then apply secondary factor
                qty_in_product_uom = uom_line._compute_quantity(
                    qty=1.0,
                    to_unit=product_uom,
                    round=False,
                )
                # The secondary unit factor is relative to product UOM
                factor = qty_in_product_uom * self.secondary_uom_id.factor
            else:
                # Different categories, use the original logic (multiply factors)
                # This maintains backward compatibility for cases with different categories
                factor = self.secondary_uom_id.factor * uom_line.factor
        
        return factor

    def _get_quantity_from_line(self):
        return self[self._secondary_unit_fields["qty_field"]]

    @api.model
    def _get_secondary_uom_qty_depends(self):
        if not self._secondary_unit_fields:
            return []
        return [self._secondary_unit_fields["qty_field"]]

    @api.depends(lambda x: x._get_secondary_uom_qty_depends())
    def _compute_secondary_uom_qty(self):
        for line in self:
            if not line.secondary_uom_id:
                line.secondary_uom_qty = 0.0
                continue
            elif line.secondary_uom_id.dependency_type == "independent":
                continue
            factor = line._get_factor_line()
            qty_line = line._get_quantity_from_line()
            qty = float_round(
                qty_line / (factor or 1.0),
                precision_rounding=line.secondary_uom_id.uom_id.rounding,
            )
            line.secondary_uom_qty = qty

    def _get_default_value_for_qty_field(self):
        return self.default_get([self._secondary_unit_fields["qty_field"]]).get(
            self._secondary_unit_fields["qty_field"]
        )

    def _compute_helper_target_field_qty(self):
        """Set the target qty field defined in model"""
        default_qty_field_value = self._get_default_value_for_qty_field()
        for rec in self:
            if not rec.secondary_uom_id:
                rec[rec._secondary_unit_fields["qty_field"]] = (
                    rec[rec._secondary_unit_fields["qty_field"]]
                    or default_qty_field_value
                )
                continue
            if rec.secondary_uom_id.dependency_type == "independent":
                if rec[rec._secondary_unit_fields["qty_field"]] == 0.0:
                    rec[rec._secondary_unit_fields["qty_field"]] = (
                        default_qty_field_value
                    )
                continue
            # To avoid recompute secondary_uom_qty field when
            # secondary_uom_id changes.
            rec.env.remove_to_compute(
                field=rec._fields["secondary_uom_qty"], records=rec
            )
            factor = rec._get_factor_line()
            qty = float_round(
                rec.secondary_uom_qty * factor,
                precision_rounding=rec._get_uom_line().rounding,
            )
            rec[rec._secondary_unit_fields["qty_field"]] = qty

    def _onchange_helper_product_uom_for_secondary(self):
        """Helper method to be called from onchange method of uom field in
        target model.
        """
        if not self.secondary_uom_id:
            self.secondary_uom_qty = 0.0
            return
        elif self.secondary_uom_id.dependency_type == "independent":
            return
        factor = self._get_factor_line()
        line_qty = self._get_quantity_from_line()
        qty = float_round(
            line_qty / (factor or 1.0),
            precision_rounding=self.secondary_uom_id.uom_id.rounding,
        )
        self.secondary_uom_qty = qty

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        if self.secondary_uom_id and not self.env.context.get(
            "skip_default_secondary_uom_qty", False
        ):
            defaults["secondary_uom_qty"] = 1.0
        return defaults
