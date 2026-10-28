"""Licença por chave — validação local (offline) com ed25519.

Formato da chave:  VOX.<blocos em base64url>
Exemplo:  VOX.MTIzNDU2Nzg5.QkxNT1BTV1ZTV1pX.QldYSUpLTE1OT1BR

O app guarda apenas a chave pública. A chave privada fica com o vendedor
(gerada por tools/make_license.py) e nunca entra no repositório.
"""
import base64
import os
import struct
from datetime import date

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# Chave pública (hex). Troque pela sua em tools/make_license.py --init
PUBLIC_KEY_HEX = os.getenv(
    "VOX_LICENSE_PUBLIC_KEY",
    "14561f63406bd0912e6c422f9f0abeb58b75a6cd2ee6c0784f2f3668519c8cb7",
)

TIERS = {"trial": 0, "pessoal": 1, "pro": 2}
TIER_NAMES = {v: k for k, v in TIERS.items()}

_PREFIX = "VOX"
_SIG_BYTES = 64
_PAYLOAD_BYTES = 9  # versão(1) + tier(1) + validade em dias(2) + serial(5)


class LicenseError(Exception):
    """Chave inválida ou expirada."""


def _public_key() -> Ed25519PublicKey:
    raw = bytes.fromhex(PUBLIC_KEY_HEX)
    if len(raw) != 32:
        raise LicenseError("Chave pública do app inválida.")
    return Ed25519PublicKey.from_public_bytes(raw)


def normalize(key: str) -> str:
    """Remove prefixo, espacos e hifens; devolve só o base64url."""
    if not key:
        return ""
    k = key.strip()
    if k.upper().startswith(_PREFIX):
        k = k[len(_PREFIX):]
    return "".join(c for c in k if c.isalnum() or c in "-_")


def sign_payload(payload: bytes, private_key) -> bytes:
    return private_key.sign(payload)


def make_key(private_key, tier: str = "pro", days: int = 0, serial: bytes | None = None) -> str:
    """Gera uma chave de licença (uso do vendedor)."""
    if tier not in TIERS:
        raise LicenseError(f"Plano inválido: {tier}")
    serial = serial or os.urandom(5)
    payload = struct.pack("!BBH", 1, TIERS[tier], days) + serial
    blob = payload + sign_payload(payload, private_key)
    b64 = base64.urlsafe_b64encode(blob).decode().rstrip("=")
    blocks = [b64[i:i + 13] for i in range(0, len(b64), 13)]
    return f"{_PREFIX}." + ".".join(blocks)


def verify(key: str) -> dict:
    """Valida a chave e devolve {tier, expira_em, serial}. Levanta LicenseError se inválida."""
    b64 = normalize(key)
    if not b64:
        raise LicenseError("Chave vazia.")
    try:
        blob = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
    except Exception as e:  # noqa: BLE001
        raise LicenseError("Chave mal formatada.") from e
    if len(blob) != _PAYLOAD_BYTES + _SIG_BYTES:
        raise LicenseError("Chave inválida.")
    payload, sig = blob[:_PAYLOAD_BYTES], blob[_PAYLOAD_BYTES:]
    try:
        _public_key().verify(sig, payload)
    except InvalidSignature as e:
        raise LicenseError("Chave inválida.") from e

    version, tier_code, days = struct.unpack("!BBH", payload[:4])
    if version != 1:
        raise LicenseError("Versão de chave não suportada.")
    serial = payload[4:].hex()
    tier = TIER_NAMES.get(tier_code, "pro")
    expires = None
    if days:
        expires = date.today().toordinal() + days
    return {"tier": tier, "expira_em": expires, "serial": serial,
            "dias_restantes": days if days else None}
