"""Testes do carregador de .env."""

from __future__ import annotations

import os

import pytest

from totvs_sync.ambiente import carregar_dotenv


@pytest.fixture(autouse=True)
def limpar_ambiente():
    """Remove as chaves de teste antes e depois, para não vazar entre testes."""
    chaves = ["TST_A", "TST_B", "TST_C", "TST_EXPORT", "TST_ASPAS"]
    for chave in chaves:
        os.environ.pop(chave, None)
    yield
    for chave in chaves:
        os.environ.pop(chave, None)


def escrever(tmp_path, conteudo: str):
    caminho = tmp_path / ".env"
    caminho.write_text(conteudo, encoding="utf-8")
    return caminho


def test_define_variaveis_do_arquivo(tmp_path):
    arquivo = escrever(tmp_path, "TST_A=um\nTST_B=dois\n")

    assert carregar_dotenv(arquivo) == 2
    assert os.environ["TST_A"] == "um"
    assert os.environ["TST_B"] == "dois"


def test_ambiente_ja_definido_tem_precedencia(tmp_path):
    """O systemd ou o CI injetam a senha; um .env esquecido não pode sobrepor."""
    os.environ["TST_A"] = "do ambiente"
    arquivo = escrever(tmp_path, "TST_A=do arquivo\n")

    assert carregar_dotenv(arquivo) == 0
    assert os.environ["TST_A"] == "do ambiente"


def test_ignora_comentarios_e_linhas_em_branco(tmp_path):
    arquivo = escrever(tmp_path, "# comentário\n\n  \nTST_A=um\n# outro\n")

    assert carregar_dotenv(arquivo) == 1


def test_remove_prefixo_export(tmp_path):
    arquivo = escrever(tmp_path, "export TST_EXPORT=valor\n")

    carregar_dotenv(arquivo)
    assert os.environ["TST_EXPORT"] == "valor"


@pytest.mark.parametrize("linha", ['TST_ASPAS="com aspas"', "TST_ASPAS='com aspas'"])
def test_remove_aspas_ao_redor_do_valor(tmp_path, linha):
    carregar_dotenv(escrever(tmp_path, linha + "\n"))
    assert os.environ["TST_ASPAS"] == "com aspas"


def test_valor_com_sinal_de_igual_dentro(tmp_path):
    """Senha com '=' é comum; só o primeiro separador conta."""
    carregar_dotenv(escrever(tmp_path, "TST_C=abc=def=ghi\n"))
    assert os.environ["TST_C"] == "abc=def=ghi"


def test_valor_vazio_e_aceito(tmp_path):
    carregar_dotenv(escrever(tmp_path, "TST_A=\n"))
    assert os.environ["TST_A"] == ""


def test_linha_sem_igual_e_ignorada(tmp_path):
    assert carregar_dotenv(escrever(tmp_path, "isto nao e uma atribuicao\n")) == 0


def test_arquivo_ausente_nao_e_erro(tmp_path):
    """Em produção as variáveis vêm do systemd, não de arquivo."""
    assert carregar_dotenv(tmp_path / "nao-existe") == 0
