import hashlib
import hmac

def verify_signature(raw_body: bytes, signature_header: str | None, secret: str )-> bool:
    if not signature_header:
        return False
    if not signature_header.startswith("sha256="):
        return False

    received_hash = signature_header.removeprefix("sha256=")
    expected_hash = hmac.new(
        secret.encode(),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected_hash, received_hash)
