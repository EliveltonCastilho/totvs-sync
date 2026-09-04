"""Carga atômica: a tabela de destino nunca fica pela metade.

A troca é feita em duas fases:

1. **Estágio.** Uma tabela auxiliar com a mesma estrutura do destino recebe os
   registros em lotes. Ninguém enxerga o meio da carga e nada trava a leitura da
   tabela real enquanto ela é preenchida — que é a parte demorada.
2. **Promoção.** Numa **única transação**, o destino é esvaziado e recebe de uma vez
   o conteúdo do estágio. Se qualquer coisa falhar, o ``ROLLBACK`` devolve a tabela
   ao estado anterior.

**Por que ``DELETE`` e não ``TRUNCATE`` no destino.** ``TRUNCATE`` é o jeito óbvio
e rápido de esvaziar a tabela, e é o que a primeira versão deste sincronizador
fazia. Mas ``TRUNCATE`` é DDL, e **todo DDL no Oracle provoca commit implícito** —
inclusive do que já estava pendente na transação. Se a carga falhasse depois dele
(conexão caída, constraint violada, disco cheio), a tabela ficava **vazia em
produção** e não havia rollback que salvasse. ``DELETE`` é mais lento e gera undo,
e é exatamente esse custo que compra a atomicidade.

**Por que uma tabela de estágio comum e não uma GLOBAL TEMPORARY TABLE.** A GTT
parece a escolha natural, mas criá-la a cada execução seria DDL — o mesmo commit
implícito, no meio da carga. A GTT é feita para existir permanentemente no esquema
e ter só os *dados* privados por sessão. Como aqui a estrutura precisa acompanhar a
do destino, o estágio é uma tabela comum criada uma vez (``CREATE TABLE ... AS
SELECT ... WHERE 1=0``, que copia a estrutura) e reaproveitada depois. No estágio o
``TRUNCATE`` é bem-vindo: ele é descartável, e é justamente por isso que o commit
implícito não faz mal ali.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from .banco import Banco, identificador
from .coercao import converter
from .mapeamento import Mapeamento

__all__ = ["ResultadoCarga", "TAMANHO_LOTE_PADRAO", "carregar", "nome_estagio"]

# 1000 registros por ``executemany``. Acima disso o ganho fica marginal e o array
# de binds começa a pesar na memória do cliente e do servidor.
TAMANHO_LOTE_PADRAO = 1000

# ORA-00955: nome já usado por outro objeto — o "já existe" do Oracle.
_ORA_OBJETO_JA_EXISTE = 955

Progresso = Callable[[int, str], None]


@dataclass
class ResultadoCarga:
    """O que a carga fez, para log e para a marca d'água."""

    lidos: int = 0
    estagiados: int = 0
    promovidos: int = 0
    rejeitados: int = 0
    colunas_ignoradas: list[str] = field(default_factory=list)
    colunas_ausentes: list[str] = field(default_factory=list)


def nome_estagio(destino: str) -> str:
    """Nome da tabela de estágio de um destino, respeitando o limite do Oracle.

    Identificadores vão até 128 caracteres no 12.2+ e até 30 no 11g. O corte em 26
    mais o prefixo mantém o nome válido nos dois.
    """
    return f"STG_{identificador(destino)[:26]}"


def carregar(
    banco: Banco,
    *,
    destino: str,
    linhas: Iterable[list[str]],
    mapeamento: Mapeamento,
    tamanho_lote: int = TAMANHO_LOTE_PADRAO,
    progresso: Progresso | None = None,
) -> ResultadoCarga:
    """Carrega ``linhas`` na tabela ``destino``, substituindo o conteúdo atual.

    Args:
        banco: conexão aberta, com autocommit desligado.
        destino: tabela que receberá os dados.
        linhas: registros já lidos do CSV, na ordem do cabeçalho.
        mapeamento: de quais posições do CSV vem cada coluna do destino.
        tamanho_lote: registros por ``executemany``.
        progresso: chamado a cada lote com ``(total_acumulado, etapa)``.

    Returns:
        O :class:`ResultadoCarga` com as contagens de cada fase.

    Raises:
        ValueError: se o mapeamento estiver vazio — carregar zero colunas apagaria
            a tabela em troca de nada, então é erro, não caso de borda.
    """
    if not mapeamento:
        raise ValueError(
            f"Nenhuma coluna do CSV corresponde às colunas de {destino!r}. "
            "Cabeçalho do export mudou ou o arquivo é de outra tabela."
        )

    tabela = identificador(destino)
    estagio = nome_estagio(destino)
    colunas = mapeamento.nomes
    lista_colunas = ", ".join(colunas)

    resultado = ResultadoCarga(
        colunas_ignoradas=list(mapeamento.ignoradas),
        colunas_ausentes=list(mapeamento.ausentes),
    )

    # ---------------------------------------------------------------- 1. estágio
    # Criada uma única vez na vida do esquema; nas execuções seguintes o ORA-00955
    # é esperado e ignorado. Depois, TRUNCATE — que aqui é seguro, porque o
    # conteúdo do estágio é descartável por definição.
    banco.executar_ddl(
        f"CREATE TABLE {estagio} AS SELECT * FROM {tabela} WHERE 1 = 0",
        ignorar_erros=(_ORA_OBJETO_JA_EXISTE,),
    )
    banco.executar(f"TRUNCATE TABLE {estagio}")

    binds = ", ".join(f":{posicao}" for posicao in range(1, len(colunas) + 1))
    insercao = f"INSERT INTO {estagio} ({lista_colunas}) VALUES ({binds})"

    lote: list[tuple] = []
    for linha in linhas:
        resultado.lidos += 1
        lote.append(_montar_registro(linha, mapeamento))

        if len(lote) >= tamanho_lote:
            banco.executar_lote(insercao, lote)
            resultado.estagiados += len(lote)
            lote.clear()
            if progresso:
                progresso(resultado.estagiados, "estagiando")

    if lote:
        banco.executar_lote(insercao, lote)
        resultado.estagiados += len(lote)
        if progresso:
            progresso(resultado.estagiados, "estagiando")

    banco.commit()  # fecha o estágio; a promoção começa com transação limpa

    # -------------------------------------------------------------- 2. promoção
    if progresso:
        progresso(resultado.estagiados, "promovendo")

    with banco.transacao():
        banco.executar(f"DELETE FROM {tabela}")
        resultado.promovidos = banco.executar(
            f"INSERT INTO {tabela} ({lista_colunas}) SELECT {lista_colunas} FROM {estagio}"
        )

    if progresso:
        progresso(resultado.promovidos, "concluído")

    return resultado


def _montar_registro(linha: list[str], mapeamento: Mapeamento) -> tuple:
    """Extrai e converte os campos de uma linha do CSV, na ordem das colunas."""
    return tuple(
        converter(
            linha[item.indice_csv] if item.indice_csv < len(linha) else None,
            item.data_type,
            item.escala,
        )
        for item in mapeamento.colunas
    )
