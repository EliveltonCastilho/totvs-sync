"""Sincronizador de exportações CSV de ERP para MySQL.

Uso típico::

    from pathlib import Path
    from totvs_sync import Banco, ConfiguracaoBanco, Tabela, sincronizar

    with Banco(ConfiguracaoBanco.do_ambiente()) as banco:
        resultado = sincronizar(
            banco,
            Tabela(nome="erp_produto", arquivo="SB1.csv", prefixo_dicionario="B1_"),
            Path("/mnt/erp/exportacao"),
        )
"""

from __future__ import annotations

from .banco import Banco, ConfiguracaoBanco
from .carga import ResultadoCarga, carregar
from .coercao import converter
from .configuracao import Configuracao, carregar_configuracao
from .leitor_csv import LeitorExportacao, Rejeicao
from .mapeamento import CampoDicionario, ColunaMapeada, Dicionario, Mapeamento, mapear
from .marca_dagua import MarcaDagua
from .normalizacao import normalizar
from .sincronizador import Resultado, Tabela, sincronizar, sincronizar_todas

__version__ = "1.0.0"

__all__ = [
    "Banco",
    "CampoDicionario",
    "ColunaMapeada",
    "Configuracao",
    "ConfiguracaoBanco",
    "Dicionario",
    "LeitorExportacao",
    "Mapeamento",
    "MarcaDagua",
    "Rejeicao",
    "Resultado",
    "ResultadoCarga",
    "Tabela",
    "__version__",
    "carregar",
    "carregar_configuracao",
    "converter",
    "mapear",
    "normalizar",
    "sincronizar",
    "sincronizar_todas",
]
