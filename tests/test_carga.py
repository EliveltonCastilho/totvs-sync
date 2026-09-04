"""Testes da carga atômica, com um banco falso que só registra o SQL emitido.

A ordem e a natureza dos comandos são o que importa aqui: é ela que garante que a
tabela de destino nunca fica vazia por acidente. Um teste contra banco real está em
``test_integracao_oracle.py``.
"""

from __future__ import annotations

import pytest

from totvs_sync.banco import identificador
from totvs_sync.carga import carregar, nome_estagio
from totvs_sync.mapeamento import mapear

DESTINO = [
    ("B1_COD", "VARCHAR2", None),
    ("B1_DESC", "VARCHAR2", None),
    ("B1_PRECO", "NUMBER", 2),
]


class BancoFalso:
    """Registra o que seria executado, sem banco nenhum atrás."""

    def __init__(self, falhar_em: str | None = None) -> None:
        self.comandos: list[str] = []
        self.lotes: list[list[tuple]] = []
        self.commits = 0
        self.rollbacks = 0
        self._falhar_em = falhar_em

    def executar(self, sql: str, params=None) -> int:
        self._registrar(sql)
        return 3

    def executar_ddl(self, sql: str, ignorar_erros=()) -> bool:
        self._registrar(sql)
        return True

    def executar_lote(self, sql: str, registros: list[tuple]) -> int:
        self._registrar(sql)
        self.lotes.append(list(registros))
        return len(registros)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def transacao(self):
        from contextlib import contextmanager

        @contextmanager
        def _ctx():
            try:
                yield self
            except BaseException:
                self.rollback()
                raise
            else:
                self.commit()

        return _ctx()

    def _registrar(self, sql: str) -> None:
        normalizado = " ".join(sql.split())
        self.comandos.append(normalizado)
        if self._falhar_em and self._falhar_em in normalizado:
            raise RuntimeError("falha simulada")


LINHAS = [
    ["P1", "Parafuso", "1,50"],
    ["P2", "Porca", "2,25"],
    ["P3", "Arruela", "0,75"],
]


def mapa_completo():
    return mapear(["B1_COD", "B1_DESC", "B1_PRECO"], DESTINO)


def test_carga_completa_conta_cada_fase():
    banco = BancoFalso()
    resultado = carregar(
        banco, destino="ERP_PRODUTO", linhas=iter(LINHAS), mapeamento=mapa_completo()
    )

    assert resultado.lidos == 3
    assert resultado.estagiados == 3
    assert resultado.promovidos == 3


def test_valores_sao_convertidos_antes_do_bind():
    from decimal import Decimal

    banco = BancoFalso()
    carregar(banco, destino="ERP_PRODUTO", linhas=iter(LINHAS), mapeamento=mapa_completo())

    assert banco.lotes[0][0] == ("P1", "Parafuso", Decimal("1.50"))


def test_destino_e_esvaziado_com_delete_e_nunca_com_truncate():
    """TRUNCATE é DDL: commita implicitamente e não pode sofrer rollback."""
    banco = BancoFalso()
    carregar(banco, destino="ERP_PRODUTO", linhas=iter(LINHAS), mapeamento=mapa_completo())

    assert "DELETE FROM ERP_PRODUTO" in banco.comandos
    assert not any(
        comando.startswith("TRUNCATE TABLE ERP_PRODUTO") for comando in banco.comandos
    ), "o destino nunca pode ser truncado"


def test_estagio_pode_ser_truncado_porque_e_descartavel():
    banco = BancoFalso()
    carregar(banco, destino="ERP_PRODUTO", linhas=iter(LINHAS), mapeamento=mapa_completo())

    assert f"TRUNCATE TABLE {nome_estagio('ERP_PRODUTO')}" in banco.comandos


def test_delete_e_insert_do_destino_ficam_na_mesma_transacao():
    banco = BancoFalso()
    carregar(banco, destino="ERP_PRODUTO", linhas=iter(LINHAS), mapeamento=mapa_completo())

    delete = next(
        i for i, c in enumerate(banco.comandos) if c.startswith("DELETE FROM ERP_PRODUTO")
    )
    insert = next(
        i for i, c in enumerate(banco.comandos) if c.startswith("INSERT INTO ERP_PRODUTO (")
    )

    assert insert == delete + 1, "nada pode acontecer entre esvaziar e repovoar"


def test_falha_na_promocao_faz_rollback_e_nao_commita():
    banco = BancoFalso(falhar_em="INSERT INTO ERP_PRODUTO (")
    commits_antes = None

    with pytest.raises(RuntimeError):
        carregar(banco, destino="ERP_PRODUTO", linhas=iter(LINHAS), mapeamento=mapa_completo())
        commits_antes = banco.commits

    assert banco.rollbacks == 1
    assert commits_antes is None


def test_estagio_e_criado_a_partir_da_estrutura_do_destino():
    banco = BancoFalso()
    carregar(banco, destino="ERP_PRODUTO", linhas=iter(LINHAS), mapeamento=mapa_completo())

    estagio = nome_estagio("ERP_PRODUTO")
    assert f"CREATE TABLE {estagio} AS SELECT * FROM ERP_PRODUTO WHERE 1 = 0" in banco.comandos


def test_lotes_respeitam_o_tamanho_configurado():
    linhas = [[f"P{i}", f"Item {i}", "1,00"] for i in range(250)]
    banco = BancoFalso()

    carregar(
        banco, destino="ERP_PRODUTO", linhas=iter(linhas),
        mapeamento=mapa_completo(), tamanho_lote=100,
    )

    assert [len(lote) for lote in banco.lotes] == [100, 100, 50]


def test_arquivo_vazio_ainda_esvazia_o_destino():
    """Export sem linhas significa 'não há mais nada', não 'não faça nada'."""
    banco = BancoFalso()
    resultado = carregar(banco, destino="ERP_PRODUTO", linhas=iter([]), mapeamento=mapa_completo())

    assert resultado.lidos == 0
    assert "DELETE FROM ERP_PRODUTO" in banco.comandos


def test_mapeamento_vazio_e_erro_e_nao_apaga_nada():
    banco = BancoFalso()
    vazio = mapear(["nada", "a_ver"], DESTINO)

    with pytest.raises(ValueError, match="Nenhuma coluna"):
        carregar(banco, destino="ERP_PRODUTO", linhas=iter(LINHAS), mapeamento=vazio)

    assert banco.comandos == []


def test_progresso_e_chamado_nas_etapas():
    etapas = []
    banco = BancoFalso()

    carregar(
        banco, destino="ERP_PRODUTO", linhas=iter(LINHAS),
        mapeamento=mapa_completo(), progresso=lambda n, etapa: etapas.append(etapa),
    )

    assert etapas == ["estagiando", "promovendo", "concluído"]


def test_colunas_faltantes_e_sobrando_sao_reportadas():
    banco = BancoFalso()
    mapa = mapear(["B1_COD", "SOBRANDO"], DESTINO)

    resultado = carregar(banco, destino="ERP_PRODUTO", linhas=iter([["P1", "x"]]), mapeamento=mapa)

    assert resultado.colunas_ignoradas == ["SOBRANDO"]
    assert resultado.colunas_ausentes == ["B1_DESC", "B1_PRECO"]


# --------------------------------------------------------- nomes de objeto


def test_identificador_normaliza_para_maiusculo():
    assert identificador("erp_produto") == "ERP_PRODUTO"


@pytest.mark.parametrize(
    "invalido",
    ["", "1tabela", "tabela; DROP TABLE x", "tabela-com-hifen", "a" * 129, "tab ela"],
)
def test_identificador_rejeita_nome_invalido(invalido):
    """Nome de objeto entra em SQL por interpolação; a validação é o que a torna segura."""
    with pytest.raises(ValueError, match="inválido"):
        identificador(invalido)


def test_nome_do_estagio_cabe_no_limite_do_oracle():
    longo = "T" + "A" * 100
    assert len(nome_estagio(longo)) <= 30
    assert nome_estagio(longo).startswith("STG_")
