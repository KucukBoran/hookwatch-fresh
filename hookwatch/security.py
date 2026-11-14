import hashlib
import hmac
import re


def sign(secret: str, timestamp: str, raw: bytes) -> str:
    message = timestamp.encode() + b"." + raw
    return "sha256=" + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify(secret: str, timestamp: str, signature: str, raw: bytes, now: float) -> bool:
    if not re.fullmatch(r"\d{1,12}", timestamp) or abs(now - int(timestamp)) > 300:
        return False
    if not re.fullmatch(r"sha256=[0-9a-f]{64}", signature):
        return False
    return hmac.compare_digest(sign(secret, timestamp, raw), signature)
