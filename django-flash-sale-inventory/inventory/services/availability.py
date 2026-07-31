from django.db.models import F, Sum

from inventory.models import WarehouseStock


class AvailabilityService:
    def get_sku_available(self, sku_code, region=None):
        qs = WarehouseStock.objects.filter(
            sku__sku_code=sku_code,
        ).select_related("warehouse")
        if region:
            qs = qs.filter(warehouse__region=region)
        result = qs.aggregate(total=Sum("quantity") - Sum("reserved_quantity"))
        return result["total"] or 0

    def get_batch_availability(self, sku_codes, region=None):
        qs = WarehouseStock.objects.filter(
            sku__sku_code__in=sku_codes,
        ).select_related("warehouse", "sku")
        if region:
            qs = qs.filter(warehouse__region=region)
        aggregates = qs.values("sku__sku_code").annotate(
            total=Sum("quantity") - Sum("reserved_quantity")
        )
        result = {}
        for row in aggregates:
            result[row["sku__sku_code"]] = row["total"] or 0
        for code in sku_codes:
            result.setdefault(code, 0)
        return result

    def get_warehouse_rollup(self, warehouse_code):
        return list(
            WarehouseStock.objects.filter(warehouse__code=warehouse_code)
            .values("quantity", "reserved_quantity")
            .annotate(
                sku_code=F("sku__sku_code"),
                available=F("quantity") - F("reserved_quantity"),
            )
            .order_by("sku_code")
        )
