"""Carregamento do ``.env``.

Vinte linhas em vez de uma dependência: o formato que interessa aqui são pares
``CHAVE=valor``, e ``python-dotenv`` traria interpolação de variáveis, expansão de
``~`` e parsing de multilinha que este projeto não usa.

Duas regras que evitam surpresa em produção:

* **o ambiente real sempre vence.** Uma variável já exportada não é sobrescrita
  pelo arquivo — é isso que permite ao systemd ou ao CI injetarem a senha sem que
  um ``.env`` esquecido no disco tome a frente;
* **só a CLI carrega o arquivo.** Quem usa o pacote como biblioteca gerencia o
  próprio ambiente; ler arquivo do disco por conta própria seria efeito colateral
  escondido num ``import``.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["carregar_dotenv"]


def carregar_dotenv(caminho: Path | str = ".env") -> int:
    """Lê ``caminho`` e exporta o que ainda não estiver no ambiente.

    Linhas em branco e comentários (``#``) são ignorados, assim como o prefixo
    ``export``. Aspas simples ou duplas em volta do valor são removidas.

    Returns:
        Quantas variáveis foram efetivamente definidas. Arquivo ausente devolve 0
        sem erro: em produção as variáveis vêm do systemd, não de arquivo.
    """
    arquivo = Path(caminho)
    if not arquivo.is_file():
        return 0

    definidas = 0
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue

        chave, _, valor = linha.removeprefix("export ").partition("=")
        chave = chave.strip()
        valor = valor.strip().strip("\"'")

        if chave and chave not in os.environ:
            os.environ[chave] = valor
            definidas += 1

    return definidas
