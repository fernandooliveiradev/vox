#!/usr/bin/env python3
"""Instala a tradução offline EN→PT-BR (Argos Translate, sem torch/stanza)."""
import subprocess
import sys


def main():
    print("Instalando argostranslate + sentencepiece (sem dependências pesadas)...")
    subprocess.check_call([
        sys.executable, "-m", "pip", "install", "--no-deps", "-q",
        "argostranslate", "sentencepiece",
    ])
    from app import ensure_translation_package, get_translation, translate_text

    print("Baixando/instalando modelo EN→PT...")
    pkg = ensure_translation_package()
    print(f"Modelo instalado: {pkg.from_code} → {pkg.to_code}")
    get_translation()
    print("Teste:", translate_text("Hello, how are you?"))
    print("Pronto. A opção 'Traduzir para PT-BR' já pode ser usada.")


if __name__ == "__main__":
    main()
