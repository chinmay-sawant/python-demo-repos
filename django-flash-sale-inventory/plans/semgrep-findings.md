# Semgrep Findings — 2026-07-31

Scanned with `semgrep --config=auto` (1074 community rules, 296 applicable).
**4 findings total (4 blocking).**

---

## 1. `dockerfile.security.missing-user.missing-user` — django-flash-sale-inventory/Dockerfile

**Severity:** Blocking

By not specifying a `USER`, a program in the container may run as `root`. This
is a security hazard. If an attacker can control a process running as root, they
may have control over the container. Ensure that the last `USER` in a
Dockerfile is a `USER` other than `root`.

Details: https://sg.run/Gbvn

**Autofix:**
```dockerfile
USER non-root
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
```

---

## 2. `python.django.security.audit.csrf-exempt.no-csrf-exempt` — inventory/views.py:20

**Severity:** Blocking

Detected usage of `@csrf_exempt`, which indicates that there is no CSRF token
set for this route. This could lead to an attacker manipulating the user's
account and exfiltration of private data. Instead, create a function without
this decorator.

Details: https://sg.run/rd5e

**Code:**
```python
# inventory/views.py:20
@csrf_exempt
@require_POST
def batch_availability(request):
    data = json.loads(request.body)
    sku_codes = data.get('sku_codes', [])
    region = data.get('region')
    svc = AvailabilityService()
    result = svc.get_batch_availability(sku_codes, region=region)
    return JsonResponse(result)
```

---

## 3. `dockerfile.security.missing-user.missing-user` — fastapi-live-metrics-ingest/Dockerfile

Same rule as finding #1 — applies to the FastAPI Dockerfile.

**Autofix:**
```dockerfile
USER non-root
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## 4. `dockerfile.security.missing-user.missing-user` — flask-partner-webhook-relay/Dockerfile

Same rule as finding #1 — applies to the Flask Dockerfile.

**Autofix:**
```dockerfile
USER non-root
CMD ["flask", "run", "--host", "0.0.0.0", "--port", "8000"]
```
