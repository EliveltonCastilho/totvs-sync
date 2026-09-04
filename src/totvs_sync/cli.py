"""Interface de linha de comando.

Pensada para rodar sob ``cron`` ou ``systemd``: log em stderr, código de saída que
diz se algo falhou, e ``--dry-run`` para inspecionar o mapeamento antes de tocar no
banco — que é o comando que mais se usa quando o export muda de formato.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .banco import Banco, ConfiguracaoBanco
from .configuracao import Configuracao, carregar_configuracao
from .leitor_csv import LeitorExportacao
from .mapeamento import mapear
from .sincronizador import Resultado, Tabela, sincronizar

__all__ = ["main"]

logger = logging.getLogger("totvs_sync")


def _montar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="totvs-sync",
        description="Sincroniza exportações CSV de um ERP para tabelas Oracle.",
    )
    parser.add_argument(
        "-c", "--config", type=Path, default=Path("tabelas.toml"),
        help="arquivo TOML com o diretório e as tabelas (padrão: tabelas.toml)",
    )
    parser.add_argument(
        "-t", "--tabela", action="append", dest="tabelas", metavar="NOME",
        help="sincroniza só esta tabela; pode repetir. Sem isso, roda todas.",
    )
    parser.add_argument(
        "-f", "--forcar", action="store_true",
        help="recarrega mesmo que o arquivo não tenha mudado",
    )
    parser.add_argument(
        "-n", "--dry-run", action="store_true",
        help="mostra o mapeamento de colunas e sai, sem escrever no banco",
    )
    parser.add_argument(
        "--lote", type=int, default=1000, metavar="N",
        help="registros por lote na carga (padrão: 1000)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="log em nível DEBUG",
    )
    return parser


def _dry_run(
    config: Configuracao, selecionadas: list[Tabela], config_banco: ConfiguracaoBanco
) -> int:
    """Mostra o casamento de colunas sem escrever nada.

    Lê o banco — precisa dele para conhecer as colunas de destino — mas só executa
    consultas. É o comando que se usa quando o export muda de formato, para ver o
    que passou a casar e o que deixou de casar antes de deixar a carga rodar.
    """
    problemas = 0

    with Banco(config_banco) as banco:
        for tabela in selecionadas:
            arquivo = config.diretorio / tabela.arquivo
            print(f"\n{tabela.nome}  <-  {arquivo}")

            if not arquivo.exists():
                print("  ! arquivo não encontrado")
                problemas += 1
                continue

            try:
                cabecalho = LeitorExportacao(arquivo).cabecalho()
                dicionario = (
                    banco.dicionario(tabela.prefixo_dicionario)
                    if tabela.prefixo_dicionario
                    else None
                )
                mapa = mapear(cabecalho, banco.colunas_de(tabela.nome), dicionario)
            except (ValueError, OSError) as erro:
                print(f"  ! {erro}")
                problemas += 1
                continue

            if not mapa:
                print("  ! nenhuma coluna casou — a carga seria recusada")
                problemas += 1

            for item in mapa.colunas:
                escala = f",{item.escala}" if item.escala else ""
                print(f"  {item.origem_csv:<28} -> {item.coluna:<28} "
                      f"{item.data_type}{escala}")
            if mapa.ignoradas:
                print(f"  ignoradas no CSV ({len(mapa.ignoradas)}): "
                      f"{', '.join(mapa.ignoradas)}")
            if mapa.ausentes:
                print(f"  sem origem no CSV ({len(mapa.ausentes)}): "
                      f"{', '.join(mapa.ausentes)}")

    return problemas


def _relatar(resultado: Resultado) -> None:
    if not resultado.ok:
        logger.error("%s: %s", resultado.tabela, resultado.motivo)
        return

    if not resultado.carregou:
        logger.info("%s: já atualizado", resultado.tabela)
        return

    carga = resultado.carga
    assert carga is not None
    logger.info(
        "%s: %d registros | %d rejeitados | %d colunas ignoradas",
        resultado.tabela, carga.promovidos, carga.rejeitados, len(carga.colunas_ignoradas),
    )
    if resultado.log_rejeicoes:
        logger.warning("%s: rejeições em %s", resultado.tabela, resultado.log_rejeicoes)


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada. Devolve 0 se tudo correu bem, 1 se alguma tabela falhou."""
    args = _montar_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        stream=sys.stderr,
    )

    try:
        config = carregar_configuracao(args.config)
    except (FileNotFoundError, ValueError) as erro:
        logger.error("%s", erro)
        return 1

    selecionadas = config.tabelas
    if args.tabelas:
        pedidas = set(args.tabelas)
        selecionadas = [t for t in config.tabelas if t.nome in pedidas]
        desconhecidas = pedidas - {t.nome for t in selecionadas}
        if desconhecidas:
            logger.error(
                "tabela não declarada na configuração: %s", ", ".join(sorted(desconhecidas))
            )
            return 1

    try:
        config_banco = ConfiguracaoBanco.do_ambiente()
    except RuntimeError as erro:
        logger.error("%s", erro)
        return 1

    if args.dry_run:
        return 1 if _dry_run(config, selecionadas, config_banco) else 0

    falhas = 0
    with Banco(config_banco) as banco:
        for tabela in selecionadas:
            resultado = sincronizar(
                banco, tabela, config.diretorio,
                forcar=args.forcar, tamanho_lote=args.lote,
            )
            _relatar(resultado)
            falhas += 0 if resultado.ok else 1

    return 1 if falhas else 0
