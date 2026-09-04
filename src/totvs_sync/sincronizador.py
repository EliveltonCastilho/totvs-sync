"""Orquestração: junta leitura, mapeamento, carga e marca d'água.

Uma tabela é descrita por uma :class:`Tabela` — arquivo de origem, tabela de destino
e prefixo do dicionário. O sincronizador não sabe nada sobre nenhuma tabela em
particular; toda a especificidade vive na configuração, o que é o que permite
acrescentar uma tabela nova sem escrever código.

Na versão original havia um arquivo por tabela — ``leitura_SB1.py``,
``leitura_SC6.py``, um para cada — com a mesma lógica copiada quinze vezes e
divergindo aos poucos. Corrigir um bug de conversão de data significava lembrar de
quinze lugares.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .banco import Banco
from .carga import TAMANHO_LOTE_PADRAO, ResultadoCarga, carregar
from .leitor_csv import LeitorExportacao
from .mapeamento import mapear
from .marca_dagua import MarcaDagua

__all__ = ["Tabela", "Resultado", "sincronizar", "sincronizar_todas"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Tabela:
    """Uma tabela a sincronizar."""

    nome: str
    """Tabela de destino no Oracle."""

    arquivo: str
    """Nome do CSV dentro do diretório de exportação."""

    prefixo_dicionario: str = ""
    """Prefixo dos campos no dicionário do ERP, ex. ``B1_``. Vazio desliga o dicionário."""


@dataclass
class Resultado:
    """Desfecho da sincronização de uma tabela."""

    tabela: str
    carregou: bool
    motivo: str = ""
    carga: ResultadoCarga | None = None
    log_rejeicoes: Path | None = None

    @property
    def ok(self) -> bool:
        return self.motivo == ""


def sincronizar(
    banco: Banco,
    tabela: Tabela,
    diretorio: Path,
    *,
    forcar: bool = False,
    tamanho_lote: int = TAMANHO_LOTE_PADRAO,
) -> Resultado:
    """Sincroniza uma tabela, se o arquivo de origem tiver mudado.

    Args:
        banco: conexão aberta.
        tabela: o que sincronizar.
        diretorio: onde o ERP deposita os CSVs.
        forcar: ignora a marca d'água e recarrega mesmo sem mudança.
        tamanho_lote: registros por lote no estágio.

    Returns:
        Um :class:`Resultado`. Erros previsíveis — arquivo ausente, cabeçalho que
        não casa — viram ``motivo`` preenchido em vez de exceção: uma tabela que
        falha não pode interromper a sincronização das outras.
    """
    arquivo = diretorio / tabela.arquivo

    if not arquivo.exists():
        return Resultado(tabela.nome, carregou=False, motivo=f"arquivo não encontrado: {arquivo}")

    marca = MarcaDagua(banco, tabela.nome)
    marca.garantir_estrutura()

    if not forcar and not marca.precisa_carregar(arquivo):
        logger.info("%s: sem mudança desde a última carga", tabela.nome)
        return Resultado(tabela.nome, carregou=False)

    try:
        leitor = LeitorExportacao(arquivo)
        cabecalho = leitor.cabecalho()

        dicionario = (
            banco.dicionario(tabela.prefixo_dicionario) if tabela.prefixo_dicionario else None
        )
        mapeamento = mapear(cabecalho, banco.colunas_de(tabela.nome), dicionario)

        resultado_carga = carregar(
            banco,
            destino=tabela.nome,
            linhas=leitor.linhas(),
            mapeamento=mapeamento,
            tamanho_lote=tamanho_lote,
        )
    except (ValueError, OSError) as erro:
        logger.error("%s: %s", tabela.nome, erro)
        return Resultado(tabela.nome, carregou=False, motivo=str(erro))

    resultado_carga.rejeitados = len(leitor.rejeicoes)
    log_rejeicoes = leitor.gravar_rejeicoes()

    # Só agora a marca avança: se a promoção tivesse falhado, a exceção teria
    # abortado antes daqui e a próxima execução tentaria de novo.
    marca.registrar(arquivo, resultado_carga.promovidos)
    banco.commit()

    logger.info(
        "%s: %d registros promovidos (%d rejeitados, %d colunas ignoradas)",
        tabela.nome,
        resultado_carga.promovidos,
        resultado_carga.rejeitados,
        len(resultado_carga.colunas_ignoradas),
    )
    if resultado_carga.colunas_ignoradas:
        logger.debug("%s: colunas sem destino: %s", tabela.nome, resultado_carga.colunas_ignoradas)

    return Resultado(
        tabela.nome,
        carregou=True,
        carga=resultado_carga,
        log_rejeicoes=log_rejeicoes,
    )


def sincronizar_todas(
    banco: Banco,
    tabelas: list[Tabela],
    diretorio: Path,
    *,
    forcar: bool = False,
    tamanho_lote: int = TAMANHO_LOTE_PADRAO,
) -> list[Resultado]:
    """Sincroniza várias tabelas em sequência, sem que uma derrube as outras."""
    return [
        sincronizar(banco, tabela, diretorio, forcar=forcar, tamanho_lote=tamanho_lote)
        for tabela in tabelas
    ]
