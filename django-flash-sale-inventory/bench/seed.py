import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "flash_sale.settings")
import django

django.setup()

from datetime import timedelta

from django.utils import timezone
from inventory.models import SaleEvent, Sku, Warehouse, WarehouseStock


def seed():
    sale = SaleEvent.objects.create(
        name="Bench Sale",
        status=SaleEvent.Status.ACTIVE,
        start_at=timezone.now() - timedelta(hours=1),
        end_at=timezone.now() + timedelta(hours=1),
    )
    skus = [Sku.objects.create(sku_code=f"SKU{i:03d}", name=f"Sku {i}") for i in range(1, 21)]
    warehouses = [
        Warehouse.objects.create(code=f"WH{i:02d}", name=f"WH {i}", region=f"R{i % 3}")
        for i in range(1, 4)
    ]
    for sku in skus:
        for wh in warehouses:
            WarehouseStock.objects.create(
                warehouse=wh, sku=sku,
                quantity=1000 + sku.id + wh.id,
                reserved_quantity=sku.id % 50,
            )
    return sale, skus, warehouses


if __name__ == "__main__":
    sale, skus, warehouses = seed()
    print(f"seeded sale={sale.id}, {len(skus)} skus, {len(warehouses)} warehouses, "
          f"{len(skus) * len(warehouses)} stock rows")
