"""Teste de integração contra um Oracle de verdade.

Pulado quando ``ORACLE_DSN`` não está no ambiente, para que ``pytest`` continue
rodando em qualquer máquina sem banco. No CI ele roda contra um contêiner
``gvenzl/oracle-free``; localmente, contra qualquer instância — inclusive uma
Autonomous Database Always Free.

O que só um banco real prova: que o DDL está correto, que os binds nomeados casam,
que o ``MERGE`` da marca d'água funciona nas duas pernas e — o principal — que a
promoção é mesmo atômica.
"""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from totvs_sync import Banco, ConfiguracaoBanco, Tabela, mapear, sincronizar
from totvs_sync.carga import carregar, nome_estagio
from totvs_sync.marca_dagua import MarcaDagua

pytestmark = pytest.mark.skipif(
    not os.getenv("ORACLE_DSN"),
    reason="defina ORACLE_USER/ORACLE_PASSWORD/ORACLE_DSN para rodar a integração",
)

TABELA = "TST_PRODUTO"

DDL = f"""
CREATE TABLE {TABELA} (
  b1_cod    VARCHAR2(30) NOT NULL,
  b1_desc   VARCHAR2(120),
  b1_preco  NUMBER(15,4),
  b1_qtd    NUMBER(10),
  b1_dtcad  DATE,
  CONSTRAINT pk_{TABELA} PRIMARY KEY (b1_cod)
)
"""

CABECALHO = ["B1_COD", "B1_DESC", "B1_PRECO", "B1_QTD", "B1_DTCAD"]

PREAMBULO = "Relatorio de Produtos\n3 registros\n"
CONTEUDO = (
    PREAMBULO
    + ";".join(CABECALHO)
    + "\n"
    + "P1;Parafuso;1.234,56;10;31/12/2026\n"
    + "P2;Porca;2,25;20;2026-01-15\n"
    + "P3;Arruela;0,75;30;00000000\n"
)


def _limpar(banco: Banco) -> None:
    for objeto in (nome_estagio(TABELA), TABELA):
        banco.executar_ddl(f"DROP TABLE {objeto}", ignorar_erros=(942,))  # ORA-00942
    banco.executar("DELETE FROM sync_controle WHERE tabela = :1", (TABELA,))
    banco.commit()


@pytest.fixture
def banco():
    with Banco(ConfiguracaoBanco.do_ambiente()) as conexao:
        MarcaDagua(conexao, TABELA).garantir_estrutura()
        _limpar(conexao)
        conexao.executar(DDL)
        conexao.commit()
        yield conexao
        _limpar(conexao)


@pytest.fixture
def exportacao(tmp_path) -> Path:
    (tmp_path / "SB1.csv").write_text(CONTEUDO, encoding="latin-1")
    return tmp_path


def test_carga_completa_grava_os_tipos_certos(banco, exportacao):
    tabela = Tabela(nome=TABELA, arquivo="SB1.csv")
    resultado = sincronizar(banco, tabela, exportacao)

    assert resultado.ok and resultado.carregou
    assert resultado.carga.promovidos == 3

    linhas = banco.consultar(f"SELECT b1_cod, b1_desc, b1_preco, b1_qtd, b1_dtcad "
                             f"FROM {TABELA} ORDER BY b1_cod")

    assert [linha["B1_COD"] for linha in linhas] == ["P1", "P2", "P3"]
    assert linhas[0]["B1_PRECO"] == Decimal("1234.56")   # "1.234,56" no padrão BR
    assert linhas[0]["B1_QTD"] == 10
    assert linhas[0]["B1_DTCAD"].date() == date(2026, 12, 31)
    assert linhas[2]["B1_DTCAD"] is None                 # sentinela 00000000


def test_number_com_escala_volta_como_decimal_e_nao_float(banco, exportacao):
    """float não representa 0.1 exatamente: somar preço em float perde centavo."""
    sincronizar(banco, Tabela(nome=TABELA, arquivo="SB1.csv"), exportacao)

    linha = banco.consultar(
        f"SELECT b1_preco, b1_qtd FROM {TABELA} WHERE b1_cod = 'P1'"
    )[0]

    assert isinstance(linha["B1_PRECO"], Decimal)  # NUMBER(15,4)
    assert isinstance(linha["B1_QTD"], int)        # NUMBER(10) — sem escala
    assert not isinstance(linha["B1_PRECO"], float)


def test_segunda_execucao_nao_recarrega(banco, exportacao):
    tabela = Tabela(nome=TABELA, arquivo="SB1.csv")

    assert sincronizar(banco, tabela, exportacao).carregou is True
    assert sincronizar(banco, tabela, exportacao).carregou is False


def test_forcar_recarrega_mesmo_sem_mudanca(banco, exportacao):
    tabela = Tabela(nome=TABELA, arquivo="SB1.csv")
    sincronizar(banco, tabela, exportacao)

    assert sincronizar(banco, tabela, exportacao, forcar=True).carregou is True


def test_arquivo_alterado_dispara_recarga(banco, exportacao):
    import os as _os
    import time

    tabela = Tabela(nome=TABELA, arquivo="SB1.csv")
    sincronizar(banco, tabela, exportacao)

    arquivo = exportacao / "SB1.csv"
    arquivo.write_text(CONTEUDO + "P4;Bucha;9,99;40;2026-02-01\n", encoding="latin-1")
    futuro = time.time() + 5
    _os.utime(arquivo, (futuro, futuro))

    resultado = sincronizar(banco, tabela, exportacao)
    assert resultado.carregou and resultado.carga.promovidos == 4


def test_falha_na_promocao_preserva_os_dados_anteriores(banco, exportacao):
    """O ponto de existir a transação: um erro no meio não pode esvaziar a tabela."""
    tabela = Tabela(nome=TABELA, arquivo="SB1.csv")
    sincronizar(banco, tabela, exportacao)
    antes = banco.consultar(f"SELECT COUNT(*) AS total FROM {TABELA}")[0]["TOTAL"]

    # Duas linhas com a mesma chave: o INSERT da promoção viola a PK.
    (exportacao / "SB1.csv").write_text(
        PREAMBULO + ";".join(CABECALHO) + "\n"
        + "P9;Duplicado;1,00;1;2026-01-01\n"
        + "P9;Duplicado;2,00;2;2026-01-02\n",
        encoding="latin-1",
    )

    import oracledb

    with pytest.raises(oracledb.DatabaseError):
        carregar(
            banco,
            destino=TABELA,
            linhas=iter([["P9", "A", "1,00", "1", "2026-01-01"],
                         ["P9", "B", "2,00", "2", "2026-01-02"]]),
            mapeamento=mapear(CABECALHO, banco.colunas_de(TABELA)),
        )

    banco.rollback()
    depois = banco.consultar(f"SELECT COUNT(*) AS total FROM {TABELA}")[0]["TOTAL"]
    assert depois == antes, "a tabela não pode ter sido esvaziada pela carga que falhou"


def test_marca_dagua_faz_insert_e_depois_update(banco, exportacao):
    """As duas pernas do MERGE."""
    marca = MarcaDagua(banco, TABELA)
    arquivo = exportacao / "SB1.csv"

    assert marca.registrada() is None
    marca.registrar(arquivo, 3)
    banco.commit()
    primeira = marca.registrada()
    assert primeira is not None

    marca.registrar(arquivo, 7)
    banco.commit()
    linha = banco.consultar(
        "SELECT registros FROM sync_controle WHERE tabela = :1", (TABELA,)
    )[0]
    assert linha["REGISTROS"] == 7
