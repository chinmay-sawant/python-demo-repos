import json

from flask import Blueprint, current_app, jsonify, request

from app.services.ingest import IngestService

MAX_PAYLOAD_BYTES = 256 * 1024

bp = Blueprint("api", __name__, url_prefix="/api/v1")


@bp.before_request
def verify_auth():
    if request.endpoint and request.endpoint != "api.health":
        api_key = request.headers.get("X-Api-Key")
        if api_key != current_app.config["INGEST_API_KEY"]:
            return jsonify({"error": "unauthorized"}), 401


@bp.route("/health")
def health():
    return jsonify({"status": "ok"})


@bp.route("/webhooks", methods=["POST"])
def ingest_webhook():
    if not request.is_json:
        return jsonify({"error": "content-type must be application/json"}), 415

    if (
        request.content_length
        and request.content_length > current_app.config["INGEST_MAX_PAYLOAD_SIZE"]
    ):
        return jsonify({"error": "payload too large"}), 413

    raw = request.get_data()  # goslop-ignore: CWE-770
    if len(raw) > MAX_PAYLOAD_BYTES:
        return jsonify({"error": "payload too large"}), 413
    if not raw:
        return jsonify({"error": "invalid JSON"}), 400

    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return jsonify({"error": "invalid JSON"}), 400

    if not isinstance(data, dict):
        return jsonify({"error": "invalid JSON"}), 400

    event_type = data.get("event_type")
    if not event_type or not isinstance(event_type, str):
        return jsonify({"error": "event_type is required and must be a string"}), 400

    payload = data.get("payload")
    if not payload:
        return jsonify({"error": "payload is required"}), 400

    payload_str = raw.decode()

    idempotency_key = data.get("idempotency_key")

    result = IngestService.process_event(
        event_type=event_type,
        payload=payload_str,
        idempotency_key=idempotency_key,
    )

    return jsonify(result), 201 if not result.get("duplicate") else 200
