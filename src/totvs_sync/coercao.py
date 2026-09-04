"""Conversão de texto do CSV para o tipo da coluna de destino no Oracle.

Tudo que sai do export do ERP é texto. Quem manda no tipo é o dicionário do próprio
banco (``USER_TAB_COLUMNS``): para cada coluna sabemos ``DATA_TYPE`` e, no caso de
``NUMBER``, a escala — e é isso que decide como interpretar a string.

O resultado é um **objeto Python nativo** (``date``, ``datetime``, ``Decimal``,
``int``, ``str``), não uma string formatada. Isso importa no Oracle: passar
``date`` para uma coluna ``DATE`` deixa o ``python-oracledb`` fazer o bind com o
tipo certo, enquanto passar texto obrigaria a envolver cada valor num ``TO_DATE``
com máscara — mais lento, mais frágil e sensível ao ``NLS_DATE_FORMAT`` da sessão.

As armadilhas tratadas aqui são todas de campo, não de laboratório:

* **data zerada** — o ERP grava ``00000000`` para "sem data". Convertida
  ingenuamente vira erro ou, pior, ano 0; aqui vira ``NULL``.
* **três formatos de data convivendo** no mesmo arquivo, porque o export passou por
  versões diferentes do ERP.
* **decimal no padrão brasileiro** — ``1.234,56``. O ponto é separador de milhar e a
  vírgula é decimal, exatamente o contrário do que o ``Decimal`` espera.
* **NUMBER sem escala é inteiro** — ``NUMBER(10,0)`` é ``int``, ``NUMBER(15,2)`` é
  ``Decimal``. No Oracle os dois são o mesmo ``DATA_TYPE``; só a escala distingue.

Valor que não casa com nenhum formato conhecido vira ``NULL`` em vez de derrubar a
carga: um campo sujo em uma linha não pode custar o arquivo inteiro.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

__all__ = ["converter"]

# Sentinelas que o ERP usa no lugar de "sem valor".
_DATAS_NULAS = {"00000000", "0000-00-00", "0000-00-00 00:00:00", "00/00/0000"}

_FORMATOS_DATA = ("%d/%m/%Y", "%Y-%m-%d", "%Y%m%d")
_FORMATOS_DATAHORA = ("%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y%m%d%H%M%S", "%d/%m/%Y")

_TIPOS_TEXTO = {"VARCHAR2", "NVARCHAR2", "CHAR", "NCHAR", "CLOB", "NCLOB", "LONG"}
_TIPOS_NUMERICOS = {"NUMBER", "FLOAT", "BINARY_FLOAT", "BINARY_DOUBLE"}


def _converter_data(valor: str) -> date | None:
    if valor in _DATAS_NULAS:
        return None
    for formato in _FORMATOS_DATA:
        try:
            return datetime.strptime(valor, formato).date()
        except ValueError:
            continue
    return None


def _converter_datahora(valor: str) -> datetime | None:
    if valor in _DATAS_NULAS:
        return None
    for formato in _FORMATOS_DATAHORA:
        try:
            return datetime.strptime(valor, formato)
        except ValueError:
            continue
    return None


def _normalizar_numero(valor: str) -> str:
    # "1.234,56" -> "1234.56"; "1234,56" -> "1234.56"; "1234.56" fica como está.
    if "," in valor and "." in valor:
        return valor.replace(".", "").replace(",", ".")
    if "," in valor:
        return valor.replace(",", ".")
    return valor


def _converter_decimal(valor: str) -> Decimal | None:
    try:
        return Decimal(_normalizar_numero(valor))
    except InvalidOperation:
        return None


def _converter_inteiro(valor: str) -> int | None:
    # O ponto é separador de milhar e some. A vírgula é decimal: o que vem depois
    # dela é truncado, não concatenado — remover a vírgula transformaria "1,5" em
    # 15, que é o tipo de erro silencioso que só aparece no fechamento do mês.
    limpo = valor.replace(".", "").split(",", 1)[0]
    try:
        return int(limpo)
    except ValueError:
        return None


def converter(valor: str | None, data_type: str, escala: int | None = None) -> object | None:
    """Converte ``valor`` para o tipo Python que corresponde à coluna Oracle.

    Args:
        valor: o texto como veio do CSV.
        data_type: ``DATA_TYPE`` da coluna — ``VARCHAR2``, ``NUMBER``, ``DATE``,
            ``TIMESTAMP(6)`` e afins.
        escala: ``DATA_SCALE`` da coluna. Só é consultada para ``NUMBER``: escala
            ``0`` ou ausente significa inteiro, qualquer outra significa decimal.

    Returns:
        ``date``, ``datetime``, ``int``, ``Decimal``, ``str`` ou ``None``. ``None``
        para vazio, sentinela de data zerada e valor irreconhecível para o tipo.
    """
    if valor is None:
        return None

    valor = valor.strip()
    if not valor:
        return None

    tipo = data_type.strip().upper()

    if tipo == "DATE":
        return _converter_data(valor)

    if tipo.startswith("TIMESTAMP"):
        return _converter_datahora(valor)

    if tipo in _TIPOS_NUMERICOS:
        # NUMBER sem escala declarada é inteiro; com escala, decimal.
        if tipo == "NUMBER" and not escala:
            return _converter_inteiro(valor)
        return _converter_decimal(valor)

    if tipo in _TIPOS_TEXTO:
        return valor

    # Tipo desconhecido: entrega o texto e deixa o banco decidir se aceita.
    return valor
