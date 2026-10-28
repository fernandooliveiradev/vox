#!/usr/bin/env python3
"""Ferramenta do vendedor: cria o par de chaves e gera licenças.

    python tools/make_license.py --init
    python tools/make_license.py --make --tier pro
    python tools/make_license.py --make --tier pessoal --days 365

A chave privada fica em ~/.vox/license_private.key (fora do repositorio).
NUNCA envie esse arquivo para o cliente nem para o GitHub.
"""
import argparse
import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

import license  # noqa: E402

KEY_DIR = Path(os.getenv("VOX_KEY_DIR", Path.home() / ".vox"))
PRIV_PATH = KEY_DIR / "license_private.key"


def load_private() -> Ed25519PrivateKey:
    if not PRIV_PATH.exists():
        raise SystemExit("Chave privada nao encontrada. Rode primeiro: --init")
    return Ed25519PrivateKey.from_private_bytes(PRIV_PATH.read_bytes())


def do_init():
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    if PRIV_PATH.exists():
        print(f"Chave privada ja existe: {PRIV_PATH}")
    else:
        key = Ed25519PrivateKey.generate()
        PRIV_PATH.write_bytes(key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        ))
        PRIV_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)
        print(f"Chave privada criada em: {PRIV_PATH}")
    pub = load_private().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    ).hex()
    print("\nChave publica (cole em license.py -> PUBLIC_KEY_HEX):\n")
    print(pub)
    print("\nGuarde a chave privada em local seguro. Sem ela nao da para gerar novas licencas.")


def do_make(args):
    key = license.make_key(load_private(), tier=args.tier, days=args.days)
    print(key)
    print(f"\nPlano: {args.tier}" + (f" | validade: {args.dias} dias" if args.days else " | vitalicia"))


def main():
    parser = argparse.ArgumentParser(description="Gerador de licencas do Vox")
    parser.add_argument("--init", action="store_true", help="cria o par de chaves")
    parser.add_argument("--make", action="store_true", help="gera uma chave de licenca")
    parser.add_argument("--tier", default="pro", choices=list(license.TIERS.keys()))
    parser.add_argument("--days", type=int, default=0, help="0 = vitalicia")
    args = parser.parse_args()
    if args.init:
        do_init()
    elif args.make:
        do_make(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
