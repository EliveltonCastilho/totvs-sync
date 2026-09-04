"""Casamento entre as colunas do CSV e as colunas da tabela de destino.

Este é o ponto que faz o sincronizador sobreviver a mudanças no export.

O CSV pode trazer o cabeçalho de duas formas, e as duas aparecem no mesmo ambiente:

* **o código do campo** no ERP — ``B1_COD``, ``C6_QTDVEN``;
* **o nome amigável** configurado no relatório — ``Codigo``, ``Quantidade Vendida``.

Quem sabe traduzir um no outro é o **dicionário de dados do ERP**: uma tabela que
associa código do campo, tipo e descrição. Carregando esse dicionário conseguimos
aceitar as duas grafias sem manter uma lista de-para no código.

A consequência prática: quando alguém acrescenta uma coluna no relatório, ou troca o
rótulo de uma existente, a carga continua funcionando. Colunas que não casam com
nada são **ignoradas e reportadas** — nunca provocam falha, porque o export costuma
trazer campos de controle que não têm destino no banco.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .normalizacao import normalizar

__all__ = ["CampoDicionario", "Dicionario", "ColunaMapeada", "Mapeamento", "mapear"]


@dataclass(frozen=True)
class CampoDicionario:
    """Uma entrada do dicionário de dados do ERP."""

    campo: str  # código do campo, ex. "B1_COD"
    nome: str  # rótulo amigável, ex. "Codigo"
    tipo: str = ""


class Dicionario:
    """Índice de busca sobre o dicionário de dados, por código ou por rótulo."""

    def __init__(self, campos: list[CampoDicionario] | None = None) -> None:
        self._indice: dict[str, CampoDicionario] = {}
        for entrada in campos or []:
            self.adicionar(entrada)

    def adicionar(self, entrada: CampoDicionario) -> None:
        if not entrada.campo:
            return
        # O mesmo campo fica acessível pelas duas grafias.
        self._indice[normalizar(entrada.campo)] = entrada
        if entrada.nome:
            self._indice.setdefault(normalizar(entrada.nome), entrada)

    def resolver(self, coluna_csv: str) -> str | None:
        """Devolve o código do campo correspondente ao cabeçalho, se houver."""
        entrada = self._indice.get(normalizar(coluna_csv))
        return entrada.campo if entrada else None

    def __len__(self) -> int:
        return len(self._indice)


@dataclass(frozen=True)
class ColunaMapeada:
    """Uma coluna do destino e onde buscá-la na linha do CSV."""

    coluna: str
    data_type: str
    indice_csv: int
    origem_csv: str
    escala: int | None = None
    """``DATA_SCALE`` da coluna; distingue ``NUMBER`` inteiro de decimal."""


@dataclass
class Mapeamento:
    """Resultado do casamento entre cabeçalho do CSV e tabela de destino."""

    colunas: list[ColunaMapeada] = field(default_factory=list)
    ignoradas: list[str] = field(default_factory=list)
    ausentes: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.colunas)

    @property
    def nomes(self) -> list[str]:
        return [item.coluna for item in self.colunas]


def mapear(
    cabecalho_csv: list[str],
    colunas_destino: list[tuple[str, str, int | None]],
    dicionario: Dicionario | None = None,
) -> Mapeamento:
    """Casa o cabeçalho do CSV com as colunas da tabela de destino.

    Args:
        cabecalho_csv: nomes das colunas na ordem em que aparecem no arquivo.
        colunas_destino: triplas ``(nome, data_type, escala)`` vindas de
            ``user_tab_columns``, na ordem da tabela.
        dicionario: dicionário do ERP, usado quando o cabeçalho não casa
            diretamente com o nome da coluna.

    Returns:
        O :class:`Mapeamento`, com as colunas casadas, as colunas do CSV que não
        têm destino (``ignoradas``) e as colunas da tabela que o CSV não trouxe
        (``ausentes``, que ficarão com o valor padrão do banco).
    """
    tipos_por_coluna = {nome: (tipo, escala) for nome, tipo, escala in colunas_destino}
    por_nome_normalizado = {normalizar(nome): nome for nome in tipos_por_coluna}

    encontradas: dict[str, tuple[int, str]] = {}
    ignoradas: list[str] = []

    for indice, coluna_csv in enumerate(cabecalho_csv):
        destino = por_nome_normalizado.get(normalizar(coluna_csv))

        if destino is None and dicionario is not None:
            codigo = dicionario.resolver(coluna_csv)
            if codigo:
                destino = por_nome_normalizado.get(normalizar(codigo))

        # A primeira ocorrência vence: exports repetem colunas de controle no fim.
        if destino is not None and destino not in encontradas:
            encontradas[destino] = (indice, coluna_csv)
        else:
            ignoradas.append(coluna_csv)

    # A ordem segue a da tabela, não a do arquivo: é ela que vale no INSERT.
    colunas = [
        ColunaMapeada(
            coluna=nome,
            data_type=tipos_por_coluna[nome][0],
            escala=tipos_por_coluna[nome][1],
            indice_csv=encontradas[nome][0],
            origem_csv=encontradas[nome][1],
        )
        for nome, _, _ in colunas_destino
        if nome in encontradas
    ]
    ausentes = [nome for nome, _, _ in colunas_destino if nome not in encontradas]

    return Mapeamento(colunas=colunas, ignoradas=ignoradas, ausentes=ausentes)
