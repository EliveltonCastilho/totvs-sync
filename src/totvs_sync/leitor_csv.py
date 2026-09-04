"""Leitura do CSV exportado pelo ERP.

O arquivo não é um CSV bem-comportado. O que ele tem de peculiar:

1. **Três linhas de preâmbulo** antes dos dados — título do relatório, contagem de
   registros e só então o cabeçalho. O cabeçalho é a linha 3.
2. **Encoding latin-1**, não UTF-8.
3. **Delimitador ``;``**, porque a vírgula é o separador decimal.
4. **Registros quebrados em várias linhas físicas.** Um campo de observação com
   Enter dentro vira duas ou três linhas no arquivo, sem aspas delimitando. Não dá
   para confiar no parser de CSV sozinho: é preciso reconhecer que a linha está
   incompleta e continuar juntando até fechar a contagem de campos.

O contrato deste módulo é: **uma linha ruim não derruba o arquivo**. Registros que
não fecham a contagem de campos são devolvidos como rejeições, com o número da linha
física, para irem para um log — e a carga segue com o resto.

A leitura é *streaming*: o arquivo nunca é carregado inteiro na memória. Exports de
milhões de linhas eram o motivo de a versão original consumir vários GB de RAM.
"""

from __future__ import annotations

import csv
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Rejeicao", "LeitorExportacao", "LINHAS_PREAMBULO"]

# Título, contagem de registros e cabeçalho.
LINHAS_PREAMBULO = 3

# Teto de linhas físicas que podem compor um único registro lógico. Sem esse limite,
# um registro corrompido no meio do arquivo faz o buffer engolir todo o resto — é
# assim que um export inteiro vira uma única "linha inválida".
MAX_LINHAS_POR_REGISTRO = 50

try:  # Campos de observação estouram o limite padrão de 128 KB por campo.
    csv.field_size_limit(sys.maxsize)
except OverflowError:  # pragma: no cover - só ocorre em plataformas de 32 bits
    csv.field_size_limit(2**31 - 1)


@dataclass(frozen=True)
class Rejeicao:
    """Um registro que não pôde ser lido, com contexto suficiente para investigar."""

    linha: int
    motivo: str
    conteudo: str

    def __str__(self) -> str:
        return f"Linha {self.linha} ({self.motivo}): {self.conteudo}"


class LeitorExportacao:
    """Lê um CSV de exportação do ERP linha a linha.

    Args:
        caminho: arquivo a ler.
        encoding: codificação do arquivo (padrão ``latin-1``, o que o ERP gera).
        delimitador: separador de campos (padrão ``;``).
    """

    def __init__(
        self,
        caminho: Path | str,
        *,
        encoding: str = "latin-1",
        delimitador: str = ";",
    ) -> None:
        self.caminho = Path(caminho)
        self.encoding = encoding
        self.delimitador = delimitador
        self._rejeicoes: list[Rejeicao] = []

    @property
    def rejeicoes(self) -> list[Rejeicao]:
        """Registros descartados na última passagem por :meth:`linhas`."""
        return list(self._rejeicoes)

    def cabecalho(self) -> list[str]:
        """Devolve os nomes das colunas, lidos da terceira linha do arquivo."""
        with self.caminho.open("r", encoding=self.encoding, newline="") as arquivo:
            leitor = csv.reader(arquivo, delimiter=self.delimitador)
            for _ in range(LINHAS_PREAMBULO - 1):
                next(leitor, None)
            cabecalho = next(leitor, None)

        if not cabecalho:
            raise ValueError(
                f"{self.caminho}: cabeçalho não encontrado na linha {LINHAS_PREAMBULO}. "
                "O arquivo está truncado ou não é um export do ERP."
            )
        return [coluna.strip() for coluna in cabecalho]

    def linhas(self) -> Iterator[list[str]]:
        """Itera sobre os registros de dados, remontando os que vieram quebrados.

        Registros que não fecham a contagem de campos do cabeçalho vão para
        :attr:`rejeicoes` em vez de interromper a leitura.
        """
        self._rejeicoes = []
        esperados = len(self.cabecalho())

        with self.caminho.open("r", encoding=self.encoding, newline="") as arquivo:
            for _ in range(LINHAS_PREAMBULO):
                next(arquivo, None)

            acumulado: list[str] | None = None
            linha_inicial = LINHAS_PREAMBULO + 1
            linhas_acumuladas = 0

            for numero, linha_fisica, campos, seguinte in self._com_lookahead(arquivo):
                if campos is None:
                    self._rejeitar(numero, "linha ilegível", linha_fisica)
                    continue

                if acumulado is None:
                    acumulado = campos
                    linha_inicial = numero
                    linhas_acumuladas = 1
                else:
                    acumulado = _emendar(acumulado, campos)
                    linhas_acumuladas += 1

                if len(acumulado) > esperados:
                    self._rejeitar(linha_inicial, "campos demais", _juntar(acumulado))
                    acumulado = None
                    continue

                if len(acumulado) < esperados:
                    if linhas_acumuladas >= MAX_LINHAS_POR_REGISTRO:
                        self._rejeitar(linha_inicial, "registro não fecha", _juntar(acumulado))
                        acumulado = None
                    continue  # incompleto: o campo continua na próxima linha física

                # A contagem fechou — mas isso ainda não quer dizer que o registro
                # acabou. Ver :meth:`_continua_na_proxima`.
                if _continua_na_proxima(acumulado, seguinte, esperados):
                    continue

                registro = [campo.strip() for campo in acumulado]
                acumulado = None
                yield registro

            if acumulado is not None:
                self._rejeitar(linha_inicial, "incompleto no fim do arquivo", _juntar(acumulado))

    def _com_lookahead(self, arquivo):
        """Itera ``(numero, linha, campos, campos_da_proxima)``.

        O lookahead é o que resolve a ambiguidade do campo quebrado na **última**
        coluna; parsear uma vez só e carregar o resultado adiante evita parsear
        cada linha duas vezes.
        """
        anterior: tuple[int, str, list[str] | None] | None = None

        for numero, linha_fisica in enumerate(arquivo, start=LINHAS_PREAMBULO + 1):
            campos = self._parsear(linha_fisica)
            if anterior is not None:
                yield (*anterior, campos)
            anterior = (numero, linha_fisica, campos)

        if anterior is not None:
            yield (*anterior, None)

    def gravar_rejeicoes(self, destino: Path | str | None = None) -> Path | None:
        """Grava as rejeições em um log ao lado do CSV. Devolve ``None`` se não houver."""
        if not self._rejeicoes:
            return None

        caminho = (
            Path(destino)
            if destino
            else self.caminho.with_name(f"{self.caminho.stem}_linhas_invalidas.log")
        )
        caminho.write_text(
            "\n".join(str(rejeicao) for rejeicao in self._rejeicoes) + "\n",
            encoding="utf-8",
        )
        return caminho

    def _parsear(self, linha_fisica: str) -> list[str] | None:
        """Parseia **uma** linha física.

        Parsear linha a linha, e não o buffer acumulado, não é detalhe: o
        ``csv.reader`` levanta ``new-line character seen in unquoted field`` quando
        recebe uma string com ``\\n`` no meio de um campo sem aspas — que é
        exatamente o caso que se quer tratar. Passando uma linha por vez o erro
        nunca acontece, e a emenda é feita por :func:`_emendar`.
        """
        try:
            return next(csv.reader([linha_fisica.rstrip("\r\n")],
                                   delimiter=self.delimitador, quotechar='"'))
        except (csv.Error, StopIteration):
            return None

    def _rejeitar(self, linha: int, motivo: str, conteudo: str) -> None:
        self._rejeicoes.append(Rejeicao(linha, motivo, conteudo.strip()))


def _continua_na_proxima(
    acumulado: list[str], seguinte: list[str] | None, esperados: int
) -> bool:
    """Decide se um registro já com a contagem cheia ainda continua na próxima linha.

    Quando a quebra cai no **último** campo, a contagem fecha antes da hora: a
    primeira metade do texto já completa o número de colunas, e a segunda metade
    ficaria órfã — começando um "registro" espúrio que corrompe todos os seguintes
    em cascata. Contagem de campos sozinha não distingue os dois casos.

    O desempate é olhar uma linha à frente. Uma linha que inicia um registro novo
    traz a contagem completa de campos; uma continuação de texto traz menos, e
    poucos o bastante para caber no que falta. Só nesse caso ela é tratada como
    continuação.

    Fica de fora um caso que **nenhum** parser resolve: quando o texto quebrado
    também contém o delimitador sem aspas, a informação de onde termina o campo
    simplesmente não está no arquivo. Aí a emenda estouraria a contagem, o registro
    é entregue truncado e o resto vira rejeição registrada no log — que é o
    comportamento honesto, em vez de adivinhar.
    """
    if seguinte is None:
        return False
    return len(seguinte) < esperados and len(acumulado) + len(seguinte) - 1 <= esperados


def _emendar(acumulado: list[str], continuacao: list[str]) -> list[str]:
    """Junta a continuação de um registro quebrado ao que já foi lido.

    A quebra caiu **dentro** de um campo, então o último campo do que já temos e o
    primeiro da continuação são as duas metades do mesmo valor — e o ``\\n`` que os
    separava faz parte do texto.
    """
    if not continuacao:
        return acumulado
    return acumulado[:-1] + [acumulado[-1] + "\n" + continuacao[0]] + continuacao[1:]


def _juntar(campos: list[str]) -> str:
    """Reconstrói o texto de um registro para aparecer no log de rejeições."""
    return ";".join(campos)
