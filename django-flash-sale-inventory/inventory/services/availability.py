from django.db.models import Sum

from inventory.models import Sku, Warehouse, WarehouseStock


class AvailabilityService:

    def get_sku_available(self, sku_code, region=None):
        qs = WarehouseStock.objects.filter(
            sku__sku_code=sku_code,
        ).select_related('warehouse')
        if region:
            qs = qs.filter(warehouse__region=region)
        result = qs.aggregate(
            total=Sum('quantity') - Sum('reserved_quantity')
        )
        return result['total'] or 0

    def get_batch_availability(self, sku_codes, region=None):
        qs = WarehouseStock.objects.filter(
            sku__sku_code__in=sku_codes,
        ).select_related('warehouse', 'sku')
        if region:
            qs = qs.filter(warehouse__region=region)
        aggregates = qs.values('sku__sku_code').annotate(
            total=Sum('quantity') - Sum('reserved_quantity')
        )
        result = {}
        for row in aggregates:
            result[row['sku__sku_code']] = row['total'] or 0
        for code in sku_codes:
            result.setdefault(code, 0)
        return result

    def get_warehouse_rollup(self, warehouse_code):
        qs = WarehouseStock.objects.filter(
            warehouse__code=warehouse_code,
        ).select_related('sku', 'warehouse')
        result = []
        for ws in qs:
            result.append({
                'sku_code': ws.sku.sku_code,
                'quantity': ws.quantity,
                'reserved_quantity': ws.reserved_quantity,
                'available': ws.available_quantity(),
            })
        return result
