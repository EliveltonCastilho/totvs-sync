#!/usr/bin/env python3
"""Gera exports CSV sintéticos no formato do ERP, defeitos incluídos.

Sem dado de nenhuma empresa: produtos, clientes e pedidos são inventados a partir
de uma semente, então a saída é reproduzível.

O ponto do gerador não é produzir CSV bonito — é produzir CSV **como o de verdade**,
com as patologias que o sincronizador precisa aguentar:

* três linhas de preâmbulo antes do cabeçalho;
* encoding latin-1 e delimitador ``;``;
* datas em formatos diferentes no mesmo arquivo, e a sentinela ``00000000``;
* decimais no padrão brasileiro (``1.234,56``);
* campos de observação com Enter dentro, quebrando o registro em várias linhas;
* linhas com campos demais;
* cabeçalho ora com o código do campo, ora com o rótulo amigável.

Uso::

    python tools/gerar_csv_sintetico.py --saida ./exportacao --produtos 5000 --pedidos 20000
"""

from __future__ import annotations

import argparse
import random
from datetime import date, timedelta
from pathlib import Path

DELIMITADOR = ";"
ENCODING = "latin-1"

TIPOS = ["MP", "PA", "PI", "ME", "SV"]
UNIDADES = ["UN", "KG", "MT", "PC", "CX"]
FAMILIAS = [
    "Parafuso sextavado", "Porca autotravante", "Arruela lisa", "Bucha de bronze",
    "Eixo usinado", "Flange cega", "Anel de vedação", "Mola helicoidal",
    "Válvula de retenção", "Suporte soldado", "Chaveta paralela", "Pino elástico",
]
MATERIAIS = ["aço 1020", "aço inox 304", "alumínio 6061", "latão", "bronze TM23", "nylon"]

# Observações do jeito que aparecem no export. Note que a última coluna ser texto
# livre é justamente o pior caso para o parser: a quebra cai no fim do registro.
OBSERVACOES = [
    "",
    "Embalagem individual obrigatória.",
    # Quebrada em duas e três linhas, sem delimitador dentro: o leitor remonta.
    "Conferir cota crítica antes da\nliberação para a produção.",
    "Item substituto homologado em 2025.\nUsar somente com aprovação\nda engenharia.",
]

# Defeito irrecuperável, gerado de propósito e com baixa frequência: o texto tem o
# delimitador dentro **e** sem aspas, então a informação de onde o campo termina não
# existe no arquivo. Nenhum parser resolve; o esperado é virar rejeição no log.
OBSERVACAO_CORROMPIDA = "Ver NC 4471; reinspecionar\no lote antes de expedir."


def _preambulo(titulo: str, registros: int) -> str:
    """As três linhas que vêm antes do cabeçalho no export do ERP."""
    return f"{titulo}\n{registros} registros exportados\n"


def _decimal_br(valor: float, casas: int = 2) -> str:
    """Formata no padrão brasileiro: ponto de milhar, vírgula decimal."""
    texto = f"{valor:,.{casas}f}"
    return texto.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _data_variada(rng: random.Random, referencia: date) -> str:
    """Devolve a data em um dos formatos que convivem no export — ou a sentinela."""
    sorteio = rng.random()
    if sorteio < 0.05:
        return "00000000"  # o "sem data" do ERP
    if sorteio < 0.40:
        return referencia.strftime("%d/%m/%Y")
    if sorteio < 0.75:
        return referencia.strftime("%Y-%m-%d")
    return referencia.strftime("%Y%m%d")


def gerar_produtos(rng: random.Random, quantidade: int, usar_rotulos: bool) -> str:
    colunas = (
        ["Codigo", "Descricao", "Tipo", "Unidade de Medida", "Preco de Venda",
         "Saldo em Estoque", "Data de Cadastro", "Ativo", "Observacao"]
        if usar_rotulos
        else ["B1_COD", "B1_DESC", "B1_TIPO", "B1_UM", "B1_PRECO",
              "B1_ESTOQUE", "B1_DTCAD", "B1_ATIVO", "B1_OBS"]
    )

    linhas = [_preambulo("Relatorio de Cadastro de Produtos", quantidade)]
    linhas.append(DELIMITADOR.join(colunas) + "\n")

    hoje = date.today()
    for indice in range(1, quantidade + 1):
        codigo = f"{rng.choice(['MP', 'PA', 'PI'])}-{indice:06d}"
        descricao = f"{rng.choice(FAMILIAS)} {rng.randint(3, 48)}mm {rng.choice(MATERIAIS)}"
        cadastro = hoje - timedelta(days=rng.randint(0, 2000))

        campos = [
            codigo,
            descricao,
            rng.choice(TIPOS),
            rng.choice(UNIDADES),
            _decimal_br(rng.uniform(0.5, 9500), 4),
            _decimal_br(rng.uniform(0, 15000), 3),
            _data_variada(rng, cadastro),
            str(rng.choice([1, 1, 1, 0])),
            # Pode conter \n — é de propósito: exercita a remontagem do registro.
            OBSERVACAO_CORROMPIDA if rng.random() < 0.005 else rng.choice(OBSERVACOES),
        ]

        # 0,3% das linhas saem com um campo a mais, como acontece quando um valor
        # de texto contém o delimitador sem estar entre aspas.
        if rng.random() < 0.003:
            campos.append("LIXO")

        linhas.append(DELIMITADOR.join(campos) + "\n")

    return "".join(linhas)


def gerar_pedidos(rng: random.Random, quantidade: int, produtos: int) -> str:
    colunas = ["C6_NUM", "C6_ITEM", "C6_PRODUTO", "C6_QTDVEN", "C6_PRCVEN",
               "C6_VALOR", "C6_ENTREG", "C6_CLIENTE"]

    linhas = [_preambulo("Relatorio de Itens de Pedido de Venda", quantidade)]
    linhas.append(DELIMITADOR.join(colunas) + "\n")

    hoje = date.today()
    for indice in range(1, quantidade + 1):
        quantidade_vendida = rng.randint(1, 500)
        preco = rng.uniform(1.0, 4500.0)
        entrega = hoje + timedelta(days=rng.randint(-400, 180))

        campos = [
            f"PV{indice // 10 + 1:06d}",
            f"{indice % 10 + 1:02d}",
            f"{rng.choice(['MP', 'PA', 'PI'])}-{rng.randint(1, produtos):06d}",
            _decimal_br(quantidade_vendida, 3),
            _decimal_br(preco, 4),
            _decimal_br(quantidade_vendida * preco, 2),
            _data_variada(rng, entrega),
            f"CLI{rng.randint(1, 300):05d}",
        ]
        linhas.append(DELIMITADOR.join(campos) + "\n")

    return "".join(linhas)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--saida", type=Path, default=Path("exportacao"),
                        help="diretório onde gravar os CSVs (padrão: ./exportacao)")
    parser.add_argument("--produtos", type=int, default=5_000)
    parser.add_argument("--pedidos", type=int, default=20_000)
    parser.add_argument("--semente", type=int, default=42,
                        help="semente do gerador; a mesma semente gera o mesmo arquivo")
    parser.add_argument("--rotulos", action="store_true",
                        help="usa os rótulos amigáveis no cabeçalho em vez dos códigos")
    args = parser.parse_args(argv)

    rng = random.Random(args.semente)
    args.saida.mkdir(parents=True, exist_ok=True)

    produtos = args.saida / "SB1.csv"
    produtos.write_text(gerar_produtos(rng, args.produtos, args.rotulos), encoding=ENCODING)
    print(f"{produtos}  ({args.produtos} produtos)")

    pedidos = args.saida / "SC6.csv"
    pedidos.write_text(gerar_pedidos(rng, args.pedidos, args.produtos), encoding=ENCODING)
    print(f"{pedidos}  ({args.pedidos} itens de pedido)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
