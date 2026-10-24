"""Password for the Sistema view (gateway/hardware/network data).

The site's password is stored only as a salted PBKDF2-SHA256 hash, in the app table
(pk=AUTH, sk=SYSTEM) - set it with tools/set_system_password.py. A correct password
gets a token signed with that stored hash and valid for TOKEN_TTL_S; the system
routes need the token (header X-System-Token). Changing the password changes the
signing key, so every token issued before stops working.
"""
import base64
import hashlib
import hmac
import secrets
import time

ITERATIONS = 310_000
TOKEN_TTL_S = 12 * 3600


class Unauthorized(Exception):
    """Missing, wrong or expired password/token (HTTP 401)."""


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def hash_password(password: str, salt: bytes | None = None, iterations: int = ITERATIONS) -> str:
    if len(password) < 8:
        raise ValueError("password must have at least 8 characters")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    calc = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iterations))
    return hmac.compare_digest(calc, base64.b64decode(digest))


def _sign(stored: str, exp: int) -> str:
    return hmac.new(stored.encode(), f"system:{exp}".encode(), hashlib.sha256).hexdigest()


def make_token(stored: str, now: float | None = None) -> tuple[str, int]:
    """(token, expiry in epoch ms)."""
    exp = int((now or time.time()) + TOKEN_TTL_S)
    return f"{exp}.{_sign(stored, exp)}", exp * 1000


def check_token(token: str | None, stored: str, now: float | None = None) -> None:
    """Raises Unauthorized unless the token was signed with the current hash and hasn't expired."""
    try:
        exp_s, sig = (token or "").split(".", 1)
        exp = int(exp_s)
    except ValueError:
        raise Unauthorized("falta la contraseña de Sistema") from None
    if exp < (now or time.time()) or not hmac.compare_digest(sig, _sign(stored, exp)):
        raise Unauthorized("la sesión de Sistema expiró o la contraseña cambió")
