"""Testes do casamento de colunas — o que faz a carga sobreviver a mudança de export."""

from __future__ import annotations

from totvs_sync.mapeamento import CampoDicionario, Dicionario, mapear
from totvs_sync.normalizacao import normalizar

# Como vem de user_tab_columns: (nome, data_type, data_scale).
DESTINO = [
    ("B1_COD", "VARCHAR2", None),
    ("B1_DESC", "VARCHAR2", None),
    ("B1_PRECO", "NUMBER", 2),
]


def test_normalizar_ignora_caixa_acento_e_pontuacao():
    assert normalizar("Código") == "codigo"
    assert normalizar("*PN REF*") == "pnref"
    assert normalizar("  B1_COD  ") == "b1cod"
    assert normalizar(None) == ""


def test_casa_pelo_nome_da_coluna_com_grafias_diferentes():
    mapa = mapear(["B1_COD", "b1 desc", "B1_Preco"], DESTINO)

    assert mapa.nomes == ["B1_COD", "B1_DESC", "B1_PRECO"]
    assert mapa.ignoradas == []
    assert mapa.ausentes == []


def test_casa_pelo_rotulo_amigavel_via_dicionario():
    """O export traz 'Codigo'; o banco tem 'B1_COD'. Quem liga os dois é o dicionário."""
    dicionario = Dicionario([
        CampoDicionario(campo="B1_COD", nome="Codigo"),
        CampoDicionario(campo="B1_DESC", nome="Descricao"),
    ])
    mapa = mapear(["Codigo", "Descricao"], DESTINO, dicionario)

    assert mapa.nomes == ["B1_COD", "B1_DESC"]
    assert mapa.ausentes == ["B1_PRECO"]


def test_coluna_do_csv_sem_destino_e_ignorada_e_reportada():
    mapa = mapear(["B1_COD", "CAMPO_DE_CONTROLE", "B1_DESC"], DESTINO)

    assert mapa.nomes == ["B1_COD", "B1_DESC"]
    assert mapa.ignoradas == ["CAMPO_DE_CONTROLE"]


def test_coluna_nova_no_export_nao_quebra_a_carga():
    """Alguém acrescentou um campo no relatório: a carga continua."""
    mapa = mapear(["B1_COD", "B1_DESC", "B1_PRECO", "B1_CAMPO_NOVO"], DESTINO)

    assert len(mapa.colunas) == 3
    assert mapa.ignoradas == ["B1_CAMPO_NOVO"]


def test_ordem_segue_a_tabela_e_nao_o_arquivo():
    """É a ordem da tabela que vale no INSERT; o CSV pode vir em qualquer ordem."""
    mapa = mapear(["B1_PRECO", "B1_COD", "B1_DESC"], DESTINO)

    assert mapa.nomes == ["B1_COD", "B1_DESC", "B1_PRECO"]
    assert [item.indice_csv for item in mapa.colunas] == [1, 2, 0]


def test_coluna_repetida_no_csv_usa_a_primeira_ocorrencia():
    mapa = mapear(["B1_COD", "B1_DESC", "B1_COD"], DESTINO)

    assert [item.indice_csv for item in mapa.colunas if item.coluna == "B1_COD"] == [0]
    assert mapa.ignoradas == ["B1_COD"]


def test_mapeamento_vazio_e_falsy():
    mapa = mapear(["nada", "a_ver"], DESTINO)

    assert not mapa
    assert mapa.ausentes == ["B1_COD", "B1_DESC", "B1_PRECO"]


def test_mapeamento_guarda_tipo_escala_e_origem():
    mapa = mapear(["B1_COD", "B1_Preco"], DESTINO)
    por_nome = {item.coluna: item for item in mapa.colunas}

    assert por_nome["B1_PRECO"].data_type == "NUMBER"
    assert por_nome["B1_PRECO"].escala == 2
    assert por_nome["B1_COD"].escala is None
    assert por_nome["B1_COD"].origem_csv == "B1_COD"


def test_dicionario_indexa_por_codigo_e_por_rotulo():
    dicionario = Dicionario([CampoDicionario(campo="C6_QTDVEN", nome="Quantidade Vendida")])

    assert dicionario.resolver("C6_QTDVEN") == "C6_QTDVEN"
    assert dicionario.resolver("quantidade vendida") == "C6_QTDVEN"
    assert dicionario.resolver("inexistente") is None


def test_dicionario_ignora_entrada_sem_codigo():
    assert len(Dicionario([CampoDicionario(campo="", nome="Orfa")])) == 0
