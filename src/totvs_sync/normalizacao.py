"""Normalização de nomes de coluna.

O casamento entre a coluna do CSV e a coluna da tabela nunca é exato: o mesmo campo
aparece como ``Codigo``, ``CODIGO``, ``Código`` ou ``B1_COD`` dependendo de quem
exportou. A normalização reduz tudo a um só formato — minúsculo, sem acento, só
alfanumérico — para que a comparação seja por conteúdo e não por grafia.
"""

from __future__ import annotations

import unicodedata

__all__ = ["normalizar"]


def normalizar(valor: str | None) -> str:
    """Reduz um nome de coluna à sua forma comparável.

    >>> normalizar("*PN REF*")
    'pnref'
    >>> normalizar("Código")
    'codigo'
    >>> normalizar("B1_COD")
    'b1cod'
    """
    decomposto = unicodedata.normalize("NFKD", valor or "")
    sem_acento = decomposto.encode("ascii", "ignore").decode("ascii")
    return "".join(c for c in sem_acento.lower().strip() if c.isalnum())
