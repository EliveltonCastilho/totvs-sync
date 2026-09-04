"""Testes da conversão de texto para o tipo da coluna Oracle."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from totvs_sync.coercao import converter


@pytest.mark.parametrize("vazio", ["", "   ", None])
def test_vazio_vira_null(vazio):
    assert converter(vazio, "VARCHAR2") is None


# ----------------------------------------------------------------------- datas


@pytest.mark.parametrize(
    "entrada",
    ["31/12/2026", "2026-12-31", "20261231"],
)
def test_data_aceita_os_tres_formatos_do_export(entrada):
    assert converter(entrada, "DATE") == date(2026, 12, 31)


def test_data_devolve_objeto_nativo_e_nao_texto():
    """O bind precisa ser um ``date``; texto exigiria TO_DATE com máscara."""
    assert isinstance(converter("31/12/2026", "DATE"), date)


@pytest.mark.parametrize("sentinela", ["00000000", "0000-00-00", "00/00/0000"])
def test_data_zerada_do_erp_vira_null(sentinela):
    """O ERP grava data zerada para 'sem data'; convertida ingenuamente viraria lixo."""
    assert converter(sentinela, "DATE") is None


def test_data_irreconhecivel_vira_null_em_vez_de_estourar():
    """Um campo sujo não pode custar o arquivo inteiro."""
    assert converter("32/13/2026", "DATE") is None
    assert converter("ontem", "DATE") is None


def test_data_invalida_no_calendario_vira_null():
    assert converter("31/02/2026", "DATE") is None


@pytest.mark.parametrize(
    "entrada,esperado",
    [
        ("31/12/2026 14:30:00", datetime(2026, 12, 31, 14, 30)),
        ("2026-12-31 14:30:00", datetime(2026, 12, 31, 14, 30)),
        ("31/12/2026", datetime(2026, 12, 31, 0, 0)),
    ],
)
def test_timestamp(entrada, esperado):
    assert converter(entrada, "TIMESTAMP(6)") == esperado


def test_timestamp_com_precisao_no_nome_do_tipo():
    """``user_tab_columns`` devolve 'TIMESTAMP(6)', não 'TIMESTAMP'."""
    assert converter("2026-01-01 00:00:00", "TIMESTAMP(9)") == datetime(2026, 1, 1)


def test_timestamp_zerado_vira_null():
    assert converter("0000-00-00 00:00:00", "TIMESTAMP(6)") is None


# --------------------------------------------------------------------- números


@pytest.mark.parametrize(
    "entrada,esperado",
    [
        ("1.234,56", Decimal("1234.56")),   # padrão brasileiro completo
        ("1234,56", Decimal("1234.56")),    # só vírgula decimal
        ("1234.56", Decimal("1234.56")),    # já em formato SQL
        ("-12,50", Decimal("-12.50")),
        ("1.234.567,89", Decimal("1234567.89")),
    ],
)
def test_number_com_escala_e_decimal_no_padrao_brasileiro(entrada, esperado):
    assert converter(entrada, "NUMBER", escala=2) == esperado


def test_number_sem_escala_e_inteiro():
    """No Oracle NUMBER(10,0) e NUMBER(15,2) têm o mesmo DATA_TYPE; a escala decide."""
    assert converter("1234", "NUMBER", escala=0) == 1234
    assert isinstance(converter("1234", "NUMBER", escala=0), int)


def test_number_com_escala_none_e_tratado_como_inteiro():
    assert converter("42", "NUMBER", escala=None) == 42


def test_number_inteiro_com_separador_de_milhar():
    assert converter("1.234", "NUMBER", escala=0) == 1234


def test_inteiro_com_decimal_trunca_em_vez_de_concatenar():
    """Remover a vírgula transformaria 1,5 em 15 — erro silencioso e caro."""
    assert converter("1,5", "NUMBER", escala=0) == 1
    assert converter("1.234,99", "NUMBER", escala=0) == 1234


def test_numero_invalido_vira_null():
    assert converter("R$ 12,00", "NUMBER", escala=2) is None
    assert converter("--", "NUMBER", escala=2) is None
    assert converter("12a", "NUMBER", escala=0) is None


def test_float_e_binary_double_sao_decimais():
    assert converter("1,5", "BINARY_DOUBLE") == Decimal("1.5")
    assert converter("1,5", "FLOAT") == Decimal("1.5")


# ----------------------------------------------------------------------- texto


@pytest.mark.parametrize("tipo", ["VARCHAR2", "NVARCHAR2", "CHAR", "CLOB"])
def test_texto_passa_intacto_menos_os_espacos(tipo):
    assert converter("  Parafuso sextavado  ", tipo) == "Parafuso sextavado"


def test_tipo_desconhecido_entrega_o_texto():
    assert converter("qualquer coisa", "ROWID") == "qualquer coisa"


@pytest.mark.parametrize("tipo", ["date", "Date", "DATE", " date "])
def test_data_type_e_case_insensitive(tipo):
    assert converter("31/12/2026", tipo) == date(2026, 12, 31)
