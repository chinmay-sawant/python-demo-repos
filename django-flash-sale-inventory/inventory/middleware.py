import time


class RequestTimingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.time()
        response = self.get_response(request)
        duration_ms = int((time.time() - start) * 1000)
        response['X-Request-Duration-Ms'] = str(duration_ms)
        return response


class SaleEventMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        sale_event_id = request.META.get('HTTP_X_SALE_EVENT_ID')
        if sale_event_id:
            request.sale_event_id = sale_event_id
        return self.get_response(request)
