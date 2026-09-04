"""Controle incremental: só recarrega o que mudou.

O ERP despeja os CSVs numa pasta de rede em horários próprios. Reprocessar todos os
arquivos a cada execução funciona, mas custa caro: são dezenas de tabelas, algumas
com milhões de linhas, e a maioria não muda de uma hora para a outra.

A marca d'água é simples de propósito: guardamos por tabela o ``mtime`` do arquivo
que foi carregado com sucesso. Na execução seguinte, se o ``mtime`` do arquivo não
avançou, não há o que fazer.

**Por que ``mtime`` e não hash do conteúdo.** O hash seria mais preciso — o ERP às
vezes reescreve o arquivo sem mudar nada, e o ``mtime`` faz recarregar à toa. Mas
hash exige ler o arquivo inteiro, que é justamente o custo que se quer evitar; num
export de alguns GB em pasta de rede, ler para decidir se vale a pena ler não paga.
O recarregamento desnecessário é idempotente, então o pior caso é desperdício de
tempo, não dado errado.

A marca d'água só é gravada **depois** de a carga ter sido promovida com sucesso.
Falhou no meio, a marca antiga permanece e a próxima execução tenta de novo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .banco import Banco, identificador

__all__ = ["MarcaDagua", "TABELA_CONTROLE", "DDL_TABELA_CONTROLE", "modificado_em"]

TABELA_CONTROLE = "SYNC_CONTROLE"

# ORA-00955: nome já usado por outro objeto.
_ORA_OBJETO_JA_EXISTE = 955

DDL_TABELA_CONTROLE = """
CREATE TABLE {tabela} (
  tabela            VARCHAR2(128) NOT NULL,
  data_modificacao  DATE          NOT NULL,
  registros         NUMBER(12)    DEFAULT 0 NOT NULL,
  atualizado_em     TIMESTAMP     DEFAULT SYSTIMESTAMP NOT NULL,
  CONSTRAINT pk_{tabela} PRIMARY KEY (tabela)
)
"""


@dataclass
class MarcaDagua:
    """Leitura e gravação da marca d'água de uma tabela."""

    banco: Banco
    tabela: str
    tabela_controle: str = TABELA_CONTROLE

    def garantir_estrutura(self) -> None:
        """Cria a tabela de controle se ainda não existir.

        O Oracle não tem ``CREATE TABLE IF NOT EXISTS``; o idioma é tentar criar e
        ignorar o ``ORA-00955``.
        """
        nome = identificador(self.tabela_controle)
        self.banco.executar_ddl(
            DDL_TABELA_CONTROLE.format(tabela=nome),
            ignorar_erros=(_ORA_OBJETO_JA_EXISTE,),
        )

    def registrada(self) -> datetime | None:
        """Data de modificação do arquivo carregado por último, se houver."""
        linhas = self.banco.consultar(
            f"SELECT data_modificacao FROM {identificador(self.tabela_controle)} "
            "WHERE tabela = :1",
            (identificador(self.tabela),),
        )
        return linhas[0]["DATA_MODIFICACAO"] if linhas else None

    def precisa_carregar(self, arquivo: Path) -> bool:
        """Decide se o arquivo mudou desde a última carga bem-sucedida."""
        registrada = self.registrada()
        return registrada is None or modificado_em(arquivo) > registrada

    def registrar(self, arquivo: Path, registros: int) -> None:
        """Marca a carga como concluída. Chamar só após a promoção ter dado certo.

        ``MERGE`` é o upsert do Oracle: uma ida ao banco, sem a corrida entre um
        ``SELECT`` e o ``INSERT`` que viria depois.
        """
        self.banco.executar(
            f"""
            MERGE INTO {identificador(self.tabela_controle)} alvo
            USING (SELECT :1 AS tabela, :2 AS data_modificacao, :3 AS registros FROM dual) origem
               ON (alvo.tabela = origem.tabela)
            WHEN MATCHED THEN UPDATE
                   SET alvo.data_modificacao = origem.data_modificacao,
                       alvo.registros        = origem.registros,
                       alvo.atualizado_em    = SYSTIMESTAMP
            WHEN NOT MATCHED THEN
                INSERT (tabela, data_modificacao, registros)
                VALUES (origem.tabela, origem.data_modificacao, origem.registros)
            """,
            (identificador(self.tabela), modificado_em(arquivo), registros),
        )


def modificado_em(arquivo: Path) -> datetime:
    """``mtime`` do arquivo, truncado ao segundo.

    O truncamento importa: o tipo ``DATE`` do Oracle guarda até o segundo, e sem
    truncar aqui a comparação ``>`` dispararia recarga em toda execução.
    """
    return datetime.fromtimestamp(arquivo.stat().st_mtime).replace(microsecond=0)
