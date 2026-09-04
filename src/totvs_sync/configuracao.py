"""Configuração das tabelas, em TOML.

Acrescentar uma tabela ao sincronizador é editar um arquivo, não escrever código:

.. code-block:: toml

    diretorio = "/mnt/erp/exportacao"

    [[tabela]]
    nome = "erp_produto"
    arquivo = "SB1.csv"
    prefixo_dicionario = "B1_"

``tomllib`` é da biblioteca padrão desde o Python 3.11, então isso não custa
dependência nenhuma.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .sincronizador import Tabela

__all__ = ["Configuracao", "carregar_configuracao"]


@dataclass(frozen=True)
class Configuracao:
    """Conteúdo do arquivo de configuração."""

    diretorio: Path
    tabelas: list[Tabela]


def carregar_configuracao(caminho: Path | str) -> Configuracao:
    """Lê e valida o TOML de configuração.

    Raises:
        FileNotFoundError: se o arquivo não existir.
        ValueError: se faltar campo obrigatório, apontando qual entrada está errada.
    """
    caminho = Path(caminho)
    if not caminho.exists():
        raise FileNotFoundError(f"Configuração não encontrada: {caminho}")

    dados = tomllib.loads(caminho.read_text(encoding="utf-8"))

    diretorio = dados.get("diretorio")
    if not diretorio:
        raise ValueError(f"{caminho}: falta a chave 'diretorio' (onde ficam os CSVs).")

    entradas = dados.get("tabela") or []
    if not entradas:
        raise ValueError(f"{caminho}: nenhuma tabela declarada (esperado ao menos um [[tabela]]).")

    tabelas = []
    for posicao, entrada in enumerate(entradas, start=1):
        for obrigatorio in ("nome", "arquivo"):
            if not entrada.get(obrigatorio):
                raise ValueError(
                    f"{caminho}: tabela #{posicao} sem '{obrigatorio}'."
                )
        tabelas.append(
            Tabela(
                nome=entrada["nome"],
                arquivo=entrada["arquivo"],
                prefixo_dicionario=entrada.get("prefixo_dicionario", ""),
            )
        )

    return Configuracao(diretorio=Path(diretorio).expanduser(), tabelas=tabelas)
