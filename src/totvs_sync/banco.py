"""Acesso ao Oracle: conexão, metadados e leitura do dicionário do ERP.

O resto do pacote conversa com o banco só através de :class:`Banco`. Isso mantém a
lógica interessante — leitura do CSV, mapeamento, coerção — testável sem banco
nenhum, e concentra num só lugar o que depende do driver.

**Driver.** ``python-oracledb`` em *thin mode*: fala o protocolo direto, sem exigir
o Oracle Instant Client instalado na máquina. É o que permite a imagem de CI ser
um ``python:3.12-slim`` em vez de uma imagem com cliente Oracle dentro.

**Identificadores.** O Oracle guarda nomes em maiúsculo quando não vêm entre aspas.
Como a intenção aqui é usar nomes convencionais, tudo é normalizado para maiúsculo
e escrito **sem aspas** — o que também evita criar tabelas que depois só possam ser
referenciadas entre aspas, armadilha clássica de quem gera DDL por script.

**Credenciais.** Vêm exclusivamente do ambiente. Não há valor padrão de host,
usuário ou senha no código.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from .mapeamento import CampoDicionario, Dicionario

__all__ = ["ConfiguracaoBanco", "Banco", "identificador"]

_IDENTIFICADOR_VALIDO = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$")


def identificador(nome: str) -> str:
    """Valida e normaliza um nome de objeto do banco.

    Nomes de tabela vêm da configuração, não de entrada de usuário, mas eles entram
    em SQL por interpolação — bind não funciona para identificador. A validação
    aqui é o que garante que interpolar é seguro.

    Raises:
        ValueError: se o nome não for um identificador Oracle simples.
    """
    if not _IDENTIFICADOR_VALIDO.match(nome or ""):
        raise ValueError(
            f"Nome de objeto inválido: {nome!r}. "
            "Esperado identificador Oracle (letra inicial, até 128 caracteres, "
            "apenas letras, dígitos, _, $ ou #)."
        )
    return nome.upper()


def _decimal_para_numeros_com_escala(cursor, metadata):
    """Faz ``NUMBER`` com casas decimais voltar como ``Decimal``, não ``float``.

    O padrão do ``python-oracledb`` é devolver ``NUMBER`` como ``float`` quando há
    escala. Para valor monetário isso é um erro esperando acontecer: ``float`` é
    binário e não representa ``0.1`` exatamente, então somas de preço acumulam
    diferença de centavo — e a conta bate no banco mas não no relatório.

    ``NUMBER`` sem escala continua vindo como ``int``, que é o certo para
    quantidade, código e contador.
    """
    import decimal

    import oracledb

    if metadata.type_code is oracledb.DB_TYPE_NUMBER and metadata.scale:
        return cursor.var(decimal.Decimal, arraysize=cursor.arraysize)
    return None


@dataclass(frozen=True)
class ConfiguracaoBanco:
    """Parâmetros de conexão, lidos do ambiente."""

    usuario: str
    senha: str
    dsn: str
    esquema: str = ""
    """Esquema dos objetos. Vazio significa o esquema do próprio usuário."""

    @classmethod
    def do_ambiente(cls, prefixo: str = "ORACLE_") -> ConfiguracaoBanco:
        """Monta a configuração a partir de ``ORACLE_USER``, ``ORACLE_DSN`` e afins.

        O ``DSN`` é a string de conexão: pode ser ``host:porta/serviço`` para um
        banco comum ou o alias do ``tnsnames.ora`` no caso do Autonomous Database.

        Raises:
            RuntimeError: se faltar variável obrigatória, com o nome exato do que
                falta — erro de configuração deve ser óbvio.
        """
        def exigir(sufixo: str) -> str:
            chave = f"{prefixo}{sufixo}"
            valor = os.getenv(chave)
            if not valor:
                raise RuntimeError(
                    f"Variável de ambiente {chave} não definida. "
                    "Veja .env.example para o conjunto completo."
                )
            return valor

        return cls(
            usuario=exigir("USER"),
            senha=exigir("PASSWORD"),
            dsn=exigir("DSN"),
            esquema=os.getenv(f"{prefixo}SCHEMA", ""),
        )


class Banco:
    """Conexão Oracle com os poucos helpers de que a carga precisa."""

    def __init__(self, config: ConfiguracaoBanco) -> None:
        self.config = config
        self._conexao: Any = None

    def __enter__(self) -> Banco:
        import oracledb  # importado aqui para não ser exigido nos testes unitários

        parametros: dict[str, Any] = {
            "user": self.config.usuario,
            "password": self.config.senha,
            "dsn": self.config.dsn,
        }
        # Autonomous Database com mTLS exige o wallet; sem mTLS, a conexão é TLS
        # simples e nada disso é necessário.
        if diretorio := os.getenv("ORACLE_WALLET_DIR"):
            parametros["config_dir"] = diretorio
            parametros["wallet_location"] = diretorio
            if senha := os.getenv("ORACLE_WALLET_PASSWORD"):
                parametros["wallet_password"] = senha

        self._conexao = oracledb.connect(**parametros)
        self._conexao.outputtypehandler = _decimal_para_numeros_com_escala

        if self.config.esquema:
            # Evita qualificar cada objeto no SQL.
            esquema = identificador(self.config.esquema)
            self.executar(f"ALTER SESSION SET CURRENT_SCHEMA = {esquema}")
        return self

    def __exit__(self, *_excecao: object) -> bool:
        if self._conexao is not None:
            try:
                self._conexao.close()
            finally:
                self._conexao = None
        return False

    # ------------------------------------------------------------------ consultas

    def consultar(self, sql: str, params: Sequence[Any] | None = None) -> list[dict]:
        """Executa uma consulta e devolve as linhas como dicionários."""
        with self._conexao.cursor() as cursor:
            cursor.execute(sql, list(params or ()))
            colunas = [descricao[0] for descricao in cursor.description]
            return [dict(zip(colunas, linha, strict=True)) for linha in cursor]

    def executar(self, sql: str, params: Sequence[Any] | None = None) -> int:
        with self._conexao.cursor() as cursor:
            cursor.execute(sql, list(params or ()))
            return cursor.rowcount

    def executar_lote(self, sql: str, registros: list[tuple]) -> int:
        with self._conexao.cursor() as cursor:
            cursor.executemany(sql, registros)
            return cursor.rowcount

    def executar_ddl(self, sql: str, ignorar_erros: tuple[int, ...] = ()) -> bool:
        """Executa DDL tolerando erros esperados, como "tabela já existe".

        O Oracle não tem ``CREATE TABLE IF NOT EXISTS``; o idioma equivalente é
        tentar e engolir o ``ORA-00955``. Devolve ``True`` se o DDL rodou.
        """
        import oracledb

        try:
            self.executar(sql)
            return True
        except oracledb.DatabaseError as erro:
            (detalhe,) = erro.args
            if detalhe.code in ignorar_erros:
                return False
            raise

    @contextmanager
    def transacao(self) -> Iterator[Banco]:
        """Bloco atômico: ou tudo entra, ou a tabela fica exatamente como estava."""
        try:
            yield self
        except BaseException:
            self._conexao.rollback()
            raise
        else:
            self._conexao.commit()

    def commit(self) -> None:
        self._conexao.commit()

    def rollback(self) -> None:
        self._conexao.rollback()

    # ------------------------------------------------------------------ metadados

    def colunas_de(
        self, tabela: str, ignorar: Sequence[str] = ("ID",)
    ) -> list[tuple[str, str, int | None]]:
        """Colunas da tabela como ``(nome, data_type, escala)``, na ordem da tabela.

        É o dicionário do banco que dita como cada valor do CSV será convertido;
        nada de tipo é declarado no código do sincronizador. A escala vem junto
        porque no Oracle ``NUMBER`` é inteiro ou decimal conforme ela.
        """
        excluir = {nome.upper() for nome in ignorar}
        linhas = self.consultar(
            """
            SELECT column_name, data_type, data_scale
              FROM user_tab_columns
             WHERE table_name = :1
             ORDER BY column_id
            """,
            (identificador(tabela),),
        )
        if not linhas:
            raise ValueError(
                f"Tabela {tabela!r} não existe ou não é visível para o usuário "
                f"{self.config.usuario!r}. Rode o DDL antes de sincronizar."
            )
        return [
            (linha["COLUMN_NAME"], linha["DATA_TYPE"], linha["DATA_SCALE"])
            for linha in linhas
            if linha["COLUMN_NAME"] not in excluir
        ]

    def dicionario(self, prefixo: str, tabela_dicionario: str = "ERP_DICIONARIO") -> Dicionario:
        """Carrega o dicionário de dados do ERP para os campos de um prefixo.

        O prefixo é o do módulo no ERP (``B1_`` para cadastro de produto, ``C6_``
        para itens de pedido). Se a tabela não existir, devolve um dicionário vazio:
        o casamento direto por nome ainda funciona, só fica menos tolerante.
        """
        try:
            linhas = self.consultar(
                f"SELECT campo, nome, tipo FROM {identificador(tabela_dicionario)} "
                "WHERE campo LIKE :1 ORDER BY campo",
                (f"{prefixo}%",),
            )
        except Exception:  # tabela ausente é degradação aceitável, não falha
            return Dicionario()

        return Dicionario(
            [
                CampoDicionario(
                    campo=(linha.get("CAMPO") or "").strip(),
                    nome=(linha.get("NOME") or "").strip(),
                    tipo=(linha.get("TIPO") or "").strip(),
                )
                for linha in linhas
            ]
        )
