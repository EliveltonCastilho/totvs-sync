"""Testes da leitura do CSV — a parte que mais apanha de arquivo real."""

from __future__ import annotations

import pytest

from totvs_sync.leitor_csv import MAX_LINHAS_POR_REGISTRO, LeitorExportacao


def escrever(tmp_path, conteudo: str, nome: str = "SB1.csv"):
    caminho = tmp_path / nome
    caminho.write_text(conteudo, encoding="latin-1")
    return caminho


PREAMBULO = "Relatorio de Produtos\n1500 registros\n"


def test_cabecalho_vem_da_terceira_linha(tmp_path):
    arquivo = escrever(tmp_path, PREAMBULO + "Codigo;Descricao;Preco\nP1;Parafuso;1,50\n")
    assert LeitorExportacao(arquivo).cabecalho() == ["Codigo", "Descricao", "Preco"]


def test_cabecalho_tem_espacos_removidos(tmp_path):
    arquivo = escrever(tmp_path, PREAMBULO + " Codigo ; Descricao \nP1;Parafuso\n")
    assert LeitorExportacao(arquivo).cabecalho() == ["Codigo", "Descricao"]


def test_arquivo_truncado_da_erro_explicativo(tmp_path):
    arquivo = escrever(tmp_path, "So o titulo\n")
    with pytest.raises(ValueError, match="cabeçalho não encontrado"):
        LeitorExportacao(arquivo).cabecalho()


def test_le_linhas_de_dados_ignorando_preambulo(tmp_path):
    arquivo = escrever(
        tmp_path,
        PREAMBULO + "Codigo;Descricao\nP1;Parafuso\nP2;Porca\n",
    )
    leitor = LeitorExportacao(arquivo)
    assert list(leitor.linhas()) == [["P1", "Parafuso"], ["P2", "Porca"]]
    assert leitor.rejeicoes == []


def test_acentuacao_latin1_e_preservada(tmp_path):
    arquivo = escrever(tmp_path, PREAMBULO + "Codigo;Descricao\nP1;Válvula de Ação\n")
    assert list(LeitorExportacao(arquivo).linhas()) == [["P1", "Válvula de Ação"]]


def test_registro_quebrado_em_duas_linhas_e_remontado(tmp_path):
    # O campo de observação tem um Enter no meio, sem aspas — o caso clássico.
    arquivo = escrever(
        tmp_path,
        PREAMBULO + "Codigo;Observacao;Preco\nP1;primeira parte\nsegunda parte;9,90\n",
    )
    leitor = LeitorExportacao(arquivo)
    linhas = list(leitor.linhas())

    assert linhas == [["P1", "primeira parte\nsegunda parte", "9,90"]]
    assert leitor.rejeicoes == []


def test_registro_quebrado_em_tres_linhas_e_remontado(tmp_path):
    arquivo = escrever(
        tmp_path,
        PREAMBULO + "Codigo;Observacao;Preco\nP1;a\nb\nc;1,00\n",
    )
    assert list(LeitorExportacao(arquivo).linhas()) == [["P1", "a\nb\nc", "1,00"]]


def test_linha_com_campos_demais_vira_rejeicao_e_nao_para_a_leitura(tmp_path):
    arquivo = escrever(
        tmp_path,
        PREAMBULO + "Codigo;Descricao\nP1;Parafuso\nP2;Porca;sobra;mais\nP3;Arruela\n",
    )
    leitor = LeitorExportacao(arquivo)
    linhas = list(leitor.linhas())

    assert linhas == [["P1", "Parafuso"], ["P3", "Arruela"]]
    assert len(leitor.rejeicoes) == 1
    assert leitor.rejeicoes[0].motivo == "campos demais"
    assert leitor.rejeicoes[0].linha == 5  # 3 de preâmbulo + 2ª linha de dados


def test_registro_corrompido_nao_engole_o_resto_do_arquivo(tmp_path):
    """Sem o teto de linhas por registro, o buffer consumiria tudo até o fim."""
    corrompida = "P1;sem fechar\n"
    boas = "".join(f"P{i};Item {i};9,90\n" for i in range(2, 40))
    arquivo = escrever(tmp_path, PREAMBULO + "Codigo;Descricao;Preco\n" + corrompida + boas)

    leitor = LeitorExportacao(arquivo)
    linhas = list(leitor.linhas())

    # O registro ruim é descartado depois do teto, e os seguintes são lidos.
    assert leitor.rejeicoes, "o registro corrompido deveria ter sido rejeitado"
    assert len(linhas) > 0, "as linhas boas depois do defeito não podem se perder"


def test_teto_de_juncao_e_respeitado(tmp_path):
    incompletas = "x\n" * (MAX_LINHAS_POR_REGISTRO + 5)
    arquivo = escrever(tmp_path, PREAMBULO + "A;B;C\n" + incompletas)

    leitor = LeitorExportacao(arquivo)
    list(leitor.linhas())

    assert leitor.rejeicoes
    assert leitor.rejeicoes[0].motivo == "registro não fecha"


def test_resto_incompleto_no_fim_do_arquivo_e_rejeitado(tmp_path):
    arquivo = escrever(tmp_path, PREAMBULO + "Codigo;Descricao;Preco\nP1;so isso\n")
    leitor = LeitorExportacao(arquivo)
    linhas = list(leitor.linhas())

    assert linhas == []
    assert leitor.rejeicoes[0].motivo == "incompleto no fim do arquivo"


def test_campos_entre_aspas_com_delimitador_dentro(tmp_path):
    arquivo = escrever(
        tmp_path,
        PREAMBULO + 'Codigo;Descricao\nP1;"Parafuso; sextavado"\n',
    )
    assert list(LeitorExportacao(arquivo).linhas()) == [["P1", "Parafuso; sextavado"]]


def test_arquivo_so_com_preambulo_nao_produz_linhas(tmp_path):
    arquivo = escrever(tmp_path, PREAMBULO + "Codigo;Descricao\n")
    leitor = LeitorExportacao(arquivo)
    assert list(leitor.linhas()) == []
    assert leitor.rejeicoes == []


def test_gravar_rejeicoes_cria_log_ao_lado_do_csv(tmp_path):
    arquivo = escrever(tmp_path, PREAMBULO + "Codigo;Descricao\nP1;a;b;c\n")
    leitor = LeitorExportacao(arquivo)
    list(leitor.linhas())

    log = leitor.gravar_rejeicoes()
    assert log is not None and log.exists()
    assert "campos demais" in log.read_text(encoding="utf-8")


def test_sem_rejeicoes_nao_cria_log(tmp_path):
    arquivo = escrever(tmp_path, PREAMBULO + "Codigo;Descricao\nP1;Parafuso\n")
    leitor = LeitorExportacao(arquivo)
    list(leitor.linhas())
    assert leitor.gravar_rejeicoes() is None
