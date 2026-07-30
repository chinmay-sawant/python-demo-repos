from django.contrib import admin

from .models import (
    Reservation,
    ReservationLine,
    SaleEvent,
    Sku,
    StockLedger,
    Warehouse,
    WarehouseStock,
)


@admin.register(SaleEvent)
class SaleEventAdmin(admin.ModelAdmin):
    list_display = ('name', 'status', 'start_at', 'end_at', 'created_at')
    list_filter = ('status',)
    search_fields = ('name',)


@admin.register(Sku)
class SkuAdmin(admin.ModelAdmin):
    list_display = ('sku_code', 'name')
    search_fields = ('sku_code', 'name')


@admin.register(Warehouse)
class WarehouseAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'region')
    list_filter = ('region',)
    search_fields = ('code', 'name', 'region')


@admin.register(WarehouseStock)
class WarehouseStockAdmin(admin.ModelAdmin):
    list_display = ('warehouse', 'sku', 'quantity', 'reserved_quantity', 'available_quantity')
    list_filter = ('warehouse', 'sku')
    search_fields = ('warehouse__code', 'sku__sku_code')


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = ('id', 'user_id', 'sale_event', 'status', 'created_at', 'expires_at')
    list_filter = ('status', 'sale_event')
    search_fields = ('user_id', 'idempotency_key', 'session_key')
    readonly_fields = ('id',)


@admin.register(ReservationLine)
class ReservationLineAdmin(admin.ModelAdmin):
    list_display = ('reservation', 'sku', 'warehouse', 'quantity')
    list_filter = ('sku', 'warehouse')
    search_fields = ('reservation__id', 'sku__sku_code', 'warehouse__code')


@admin.register(StockLedger)
class StockLedgerAdmin(admin.ModelAdmin):
    list_display = ('warehouse_stock', 'delta', 'reason', 'reservation', 'created_at')
    list_filter = ('reason',)
    search_fields = ('warehouse_stock__warehouse__code', 'warehouse_stock__sku__sku_code')
