"""Ressincronização depois de um registro corrompido.

Regressão de um defeito que só apareceu carregando um arquivo grande num banco
real: a sobra de um registro descartado emendava no **próximo registro bom** e
colava o fim de um texto na primeira coluna dele. A leitura não acusava nada — o
erro aparecia depois, como ``ORA-12899: value too large for column``.
"""

from __future__ import annotations

from totvs_sync.leitor_csv import LeitorExportacao

PREAMBULO = "Relatorio\n9 registros\n"
CABECALHO = "COD;DESC;OBS\n"


def escrever(tmp_path, corpo: str):
    caminho = tmp_path / "SB1.csv"
    caminho.write_text(PREAMBULO + CABECALHO + corpo, encoding="latin-1")
    return caminho


def test_sobra_de_registro_invalido_nao_contamina_o_proximo(tmp_path):
    arquivo = escrever(
        tmp_path,
        # Registro corrompido: o texto tem ';' sem aspas, então dá campos demais.
        "P1;Parafuso;Ver NC 4471; reinspecionar\n"
        "o lote antes de expedir.\n"   # <- sobra órfã do registro acima
        "P2;Porca;sem observacao\n"    # <- este não pode ser contaminado
        "P3;Arruela;ok\n",
    )

    leitor = LeitorExportacao(arquivo)
    linhas = list(leitor.linhas())

    assert ["P2", "Porca", "sem observacao"] in linhas
    assert ["P3", "Arruela", "ok"] in linhas
    assert all(not linha[0].startswith("o lote") for linha in linhas)
    assert all(len(linha[0]) <= 3 for linha in linhas), "coluna 1 recebeu texto de outro campo"


def test_a_sobra_e_registrada_como_rejeicao(tmp_path):
    arquivo = escrever(
        tmp_path,
        "P1;Parafuso;Ver NC 4471; reinspecionar\n"
        "o lote antes de expedir.\n"
        "P2;Porca;ok\n",
    )

    leitor = LeitorExportacao(arquivo)
    list(leitor.linhas())
    motivos = [rejeicao.motivo for rejeicao in leitor.rejeicoes]

    assert "campos demais" in motivos
    assert "sobra de registro inválido" in motivos


def test_varias_linhas_de_sobra_sao_todas_descartadas(tmp_path):
    arquivo = escrever(
        tmp_path,
        "P1;Parafuso;a; b\n"
        "sobra um\n"
        "sobra dois\n"
        "sobra tres\n"
        "P2;Porca;ok\n",
    )

    leitor = LeitorExportacao(arquivo)
    assert list(leitor.linhas()) == [["P2", "Porca", "ok"]]
    assert sum(r.motivo == "sobra de registro inválido" for r in leitor.rejeicoes) == 3


def test_registro_quebrado_legitimo_continua_sendo_remontado(tmp_path):
    """A ressincronização não pode atrapalhar o caso bom."""
    arquivo = escrever(
        tmp_path,
        "P1;Parafuso;primeira parte\nsegunda parte\n"
        "P2;Porca;ok\n",
    )

    assert list(LeitorExportacao(arquivo).linhas()) == [
        ["P1", "Parafuso", "primeira parte\nsegunda parte"],
        ["P2", "Porca", "ok"],
    ]
