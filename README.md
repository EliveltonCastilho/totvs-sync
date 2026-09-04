# totvs-sync

Sincronizador incremental de exportações CSV de ERP para **Oracle**, projetado para
sobreviver a arquivo sujo, cabeçalho que muda e carga que falha no meio.

[![CI](https://github.com/EliveltonCastilho/totvs-sync/actions/workflows/ci.yml/badge.svg)](https://github.com/EliveltonCastilho/totvs-sync/actions/workflows/ci.yml)

---

## O problema

ERPs de manufatura raramente expõem uma API. O que eles oferecem é um relatório
agendado que despeja um CSV numa pasta de rede — e esse CSV é um documento pensado
para humanos, não para máquinas:

| O que o arquivo faz | Por que quebra o parser ingênuo |
|---|---|
| Três linhas de preâmbulo antes do cabeçalho | `csv.DictReader` lê o título como se fossem colunas |
| Encoding latin-1, delimitador `;` | acentuação vira mojibake; vírgula é decimal, não separador |
| Registro quebrado em várias linhas físicas | um Enter numa observação vira duas linhas sem aspas |
| `00000000` no lugar de data vazia | convertido ingenuamente vira erro ou ano zero |
| `1.234,56` | o ponto é milhar e a vírgula é decimal — o inverso do esperado |
| Cabeçalho ora `B1_COD`, ora `Codigo` | quem configurou o relatório escolheu o rótulo |
| Colunas novas aparecendo sem aviso | posição fixa de coluna deixa de funcionar |

E o requisito por trás disso: a tabela de destino é lida o tempo todo por painéis e
relatórios. Ela **não pode ficar vazia**, nem por um segundo, nem quando a carga
falha.

## O que este projeto faz

Lê o CSV em *streaming*, casa as colunas com a tabela de destino consultando o
dicionário do próprio banco, converte cada valor para o tipo da coluna e substitui o
conteúdo da tabela numa transação — recarregando só o que mudou desde a última
execução.

```bash
totvs-sync --config tabelas.toml
```

```
2026-09-04 14:02:11 INFO    ERP_PRODUTO: 4961 registros promovidos (39 rejeitados, 1 colunas ignoradas)
2026-09-04 14:02:11 WARNING ERP_PRODUTO: rejeições em exportacao/SB1_linhas_invalidas.log
2026-09-04 14:02:19 INFO    ERP_PEDIDO_ITEM: 8000 registros promovidos (0 rejeitados, 0 colunas ignoradas)
2026-09-04 14:02:19 INFO    ERP_CLIENTE: já atualizado
```

---

## As decisões que valem discussão

### 1. `DELETE`, não `TRUNCATE` — e por que isso custa caro de propósito

Esvaziar a tabela antes de repovoá-la parece pedir por `TRUNCATE`: é ordens de
grandeza mais rápido. Mas `TRUNCATE` é DDL, e **todo DDL no Oracle provoca commit
implícito** — inclusive do que já estava pendente na transação.

Na prática isso significa que uma falha depois do `TRUNCATE` (conexão caída,
constraint violada, disco cheio) deixa a tabela **vazia em produção**, sem rollback
possível. `DELETE` gera undo e é mais lento, e é exatamente esse custo que compra a
atomicidade. [`carga.py`](src/totvs_sync/carga.py)

O mesmo raciocínio descarta a `GLOBAL TEMPORARY TABLE` para o estágio: criá-la a
cada execução seria DDL no meio da carga. O estágio é uma tabela comum criada uma
vez com `CREATE TABLE ... AS SELECT ... WHERE 1=0` e reaproveitada — e nela o
`TRUNCATE` é bem-vindo, porque seu conteúdo é descartável por definição.

### 2. Casar coluna pelo dicionário do ERP, não por posição

O cabeçalho pode trazer o código do campo (`B1_COD`) ou o rótulo configurado no
relatório (`Codigo`). O dicionário de dados do ERP conhece os dois, então é ele que
resolve o nome — sem lista de-para no código.

O efeito é que **acrescentar uma coluna no relatório não quebra a carga**: colunas
sem destino são ignoradas e reportadas, colunas ausentes ficam com o default do
banco. [`mapeamento.py`](src/totvs_sync/mapeamento.py)

### 3. Tipo vem do banco, não do código

Nenhum tipo é declarado no sincronizador. `user_tab_columns` diz o `DATA_TYPE` e a
escala de cada coluna, e é isso que decide como interpretar a string — inclusive o
detalhe de que, no Oracle, `NUMBER(10,0)` e `NUMBER(15,2)` têm o mesmo `DATA_TYPE`
e só a escala distingue inteiro de decimal.

A conversão devolve **objeto Python nativo** (`date`, `Decimal`, `int`), não texto
formatado: é o que deixa o `python-oracledb` fazer o bind com o tipo certo, em vez
de envolver cada valor num `TO_DATE` sensível ao `NLS_DATE_FORMAT` da sessão.
[`coercao.py`](src/totvs_sync/coercao.py)

### 4. Remontar registro quebrado exige olhar uma linha à frente

Quando um campo de texto tem um Enter dentro, o registro vira duas ou três linhas
físicas. Juntar por contagem de campos resolve o caso geral — mas não quando a
quebra cai na **última** coluna: aí a contagem fecha antes da hora, a segunda metade
do texto fica órfã e corrompe todos os registros seguintes em cascata.

O desempate é um lookahead de uma linha: quem inicia um registro novo traz a
contagem cheia de campos; uma continuação de texto traz menos.

Há um caso que **nenhum** parser resolve, e o código diz isso em vez de fingir: se o
texto quebrado também contém o delimitador sem aspas, a informação de onde o campo
termina não existe no arquivo. Esse registro vira rejeição registrada no log — não
um palpite. [`leitor_csv.py`](src/totvs_sync/leitor_csv.py)

### 5. Uma linha ruim nunca derruba o arquivo

Valor irreconhecível para o tipo vira `NULL`; registro que não fecha vira rejeição
com número da linha num `.log` ao lado do CSV. A carga segue. Numa rotina noturna
sem ninguém olhando, abortar 400 mil registros por causa de um campo sujo é o
comportamento errado.

### 6. Marca d'água por `mtime`, e por que não por hash

Guardar o hash do arquivo seria mais preciso — o ERP às vezes reescreve o arquivo
sem mudar nada. Mas hash exige ler o arquivo inteiro, que é justamente o custo que
se quer evitar. Como o recarregamento é idempotente, o pior caso do `mtime` é
desperdício de tempo, não dado errado. [`marca_dagua.py`](src/totvs_sync/marca_dagua.py)

A marca só avança **depois** da promoção bem-sucedida: falhou, a próxima execução
tenta de novo.

---

## Começando

```bash
git clone https://github.com/EliveltonCastilho/totvs-sync
cd totvs-sync
pip install -e ".[dev]"

# 1. Credenciais (nenhuma tem valor padrão no código)
cp .env.example .env && $EDITOR .env

# 2. Esquema de demonstração
sqlplus usuario/senha@dsn @examples/esquema.sql

# 3. Dados sintéticos — sem dado de empresa nenhuma, e reproduzíveis pela semente
python tools/gerar_csv_sintetico.py --saida ./exportacao --produtos 5000 --pedidos 20000

# 4. Conferir o mapeamento antes de tocar no banco
cp tabelas.exemplo.toml tabelas.toml
totvs-sync --dry-run

# 5. Sincronizar
totvs-sync
```

O gerador produz o arquivo **com os defeitos de um export real**: registros
quebrados em várias linhas, datas em três formatos, sentinela `00000000`, decimais
em padrão brasileiro e uma fração de linhas deliberadamente corrompidas. É contra
ele que se vê o tratamento de erro funcionando.

### Configuração

Acrescentar uma tabela é editar o TOML — não há código por tabela:

```toml
diretorio = "/mnt/erp/exportacao"

[[tabela]]
nome = "ERP_PRODUTO"
arquivo = "SB1.csv"
prefixo_dicionario = "B1_"
```

### Como biblioteca

```python
from pathlib import Path
from totvs_sync import Banco, ConfiguracaoBanco, Tabela, sincronizar

with Banco(ConfiguracaoBanco.do_ambiente()) as banco:
    resultado = sincronizar(
        banco,
        Tabela(nome="ERP_PRODUTO", arquivo="SB1.csv", prefixo_dicionario="B1_"),
        Path("/mnt/erp/exportacao"),
    )
    print(resultado.carga.promovidos)
```

---

## Arquitetura

```
CSV do ERP ──▶ leitor_csv ──▶ mapeamento ──▶ coercao ──▶ carga ──▶ Oracle
               (streaming,     (dicionário    (tipo vem   (estágio +
                remonta         do ERP)        do banco)   transação)
                quebrados)
                                                             │
                                          marca_dagua ◀──────┘
                                          (só grava após promover)
```

| Módulo | Responsabilidade | Depende do banco? |
|---|---|---|
| [`leitor_csv`](src/totvs_sync/leitor_csv.py) | ler o arquivo e remontar registros quebrados | não |
| [`normalizacao`](src/totvs_sync/normalizacao.py) | reduzir nomes de coluna à forma comparável | não |
| [`mapeamento`](src/totvs_sync/mapeamento.py) | casar coluna do CSV com coluna da tabela | não |
| [`coercao`](src/totvs_sync/coercao.py) | texto → tipo Python da coluna | não |
| [`carga`](src/totvs_sync/carga.py) | estágio + promoção atômica | sim |
| [`marca_dagua`](src/totvs_sync/marca_dagua.py) | controle incremental (`MERGE`) | sim |
| [`banco`](src/totvs_sync/banco.py) | conexão, metadados, dicionário | sim |
| [`sincronizador`](src/totvs_sync/sincronizador.py) | orquestração | — |

A lógica interessante não toca o banco, e é por isso que ela é testável sem um.

## Testes

```bash
pytest                                # unitários, sem banco
ORACLE_DSN=... pytest tests/test_integracao_oracle.py   # integração
```

Os unitários cobrem o parser contra cada patologia do arquivo, a conversão de cada
tipo e a **ordem dos comandos** da carga — um teste falha se alguém trocar o
`DELETE` do destino por `TRUNCATE`. Os de integração rodam contra um Oracle de
verdade (`gvenzl/oracle-free` no CI, ou uma Autonomous Database) e provam o que só
um banco prova: que a promoção é atômica e que uma falha preserva os dados
anteriores.

## Requisitos

* Python 3.11+ (usa `tomllib` da biblioteca padrão)
* Oracle 12.2+ — testado em Oracle Free 23 e Autonomous Database 19c
* `python-oracledb` em *thin mode*: **não** exige o Oracle Instant Client instalado

### Conectando a uma Autonomous Database

Com o mTLS marcado como *not required*, basta a connect string TLS do console em
`ORACLE_DSN` — sem wallet. Usando wallet, aponte `ORACLE_WALLET_DIR` para a pasta
descompactada (guardada **fora** do repositório, com permissão 700) e use o alias
do `tnsnames.ora` como DSN — `saas_tp` para carga transacional; `saas_high` e
companhia são para consulta analítica com paralelismo.

Um detalhe que costuma custar meia hora: o *thin mode* lê o `ewallet.pem`, **não**
o `cwallet.sso` de auto-login. Se o `.pem` estiver cifrado — a primeira linha diz
`BEGIN ENCRYPTED PRIVATE KEY` — a senha da wallet definida no console passa a ser
obrigatória em `ORACLE_WALLET_PASSWORD`.

## Limitações conhecidas

* **Carga é *full refresh*.** Cada execução substitui a tabela inteira. Delta real
  exigiria uma coluna de alteração confiável no export, que o ERP não fornece.
* **Texto com delimitador não-escapado é irrecuperável** — vira rejeição, como
  explicado acima. Não é contornável do lado do leitor.
* **Uma tabela por arquivo.** Export com múltiplas seções no mesmo CSV não é
  suportado.

## Contexto

Reimplementação em Oracle de um sincronizador que escrevi e mantenho em produção
numa indústria metalúrgica, onde ele carrega ~15 tabelas de um ERP TOTVS Protheus
várias vezes ao dia. O código aqui é original e usa apenas dados sintéticos; o que
foi trazido do sistema real são as decisões de engenharia — e as cicatrizes que as
motivaram, incluindo o incidente do `TRUNCATE` que dá nome à seção 1.

## Licença

MIT — veja [LICENSE](LICENSE).
