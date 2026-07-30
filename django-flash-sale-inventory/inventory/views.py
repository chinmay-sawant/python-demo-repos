import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from inventory.exceptions import ReservationNotFoundError
from inventory.services.availability import AvailabilityService
from inventory.services.reservation import ReservationService


@require_GET
def sku_availability(request, sku_code):
    region = request.GET.get('region')
    svc = AvailabilityService()
    available = svc.get_sku_available(sku_code, region=region)
    return JsonResponse({'sku_code': sku_code, 'available': available})


@csrf_exempt
@require_POST
def batch_availability(request):
    data = json.loads(request.body)
    sku_codes = data.get('sku_codes', [])
    region = data.get('region')
    svc = AvailabilityService()
    result = svc.get_batch_availability(sku_codes, region=region)
    return JsonResponse(result)


@require_GET
def warehouse_rollup(request, warehouse_code):
    svc = AvailabilityService()
    stocks = svc.get_warehouse_rollup(warehouse_code)
    return JsonResponse({'warehouse_code': warehouse_code, 'stocks': stocks})
