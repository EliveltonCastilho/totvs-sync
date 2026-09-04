-- Esquema de demonstração para o totvs-sync (Oracle).
--
-- Modela um recorte enxuto de um ERP de manufatura fictício: cadastro de produto,
-- itens de pedido de venda e o dicionário de campos que permite ao sincronizador
-- aceitar tanto o código do campo quanto o rótulo amigável no cabeçalho do CSV.
--
-- Rode como o usuário dono do esquema:
--     sqlplus usuario/senha@dsn @examples/esquema.sql

-- ---------------------------------------------------------------- produto (B1)
CREATE TABLE erp_produto (
  b1_cod      VARCHAR2(30)  NOT NULL,
  b1_desc     VARCHAR2(120),
  b1_tipo     VARCHAR2(10),
  b1_um       VARCHAR2(4),
  b1_preco    NUMBER(15,4),
  b1_estoque  NUMBER(15,3),
  b1_dtcad    DATE,
  b1_ativo    NUMBER(1),
  CONSTRAINT pk_erp_produto PRIMARY KEY (b1_cod)
);

COMMENT ON TABLE erp_produto IS 'Cadastro de produtos vindo do export SB1 do ERP.';

-- ------------------------------------------------- itens de pedido de venda (C6)
CREATE TABLE erp_pedido_item (
  c6_num      VARCHAR2(20)  NOT NULL,
  c6_item     VARCHAR2(6)   NOT NULL,
  c6_produto  VARCHAR2(30),
  c6_qtdven   NUMBER(15,3),
  c6_prcven   NUMBER(15,4),
  c6_valor    NUMBER(15,2),
  c6_entreg   DATE,
  c6_cliente  VARCHAR2(20),
  CONSTRAINT pk_erp_pedido_item PRIMARY KEY (c6_num, c6_item)
);

COMMENT ON TABLE erp_pedido_item IS 'Itens de pedido de venda vindos do export SC6.';

-- ------------------------------------------------------ dicionário de campos (SX3)
-- É esta tabela que deixa o sincronizador aceitar 'Codigo' ou 'B1_COD' no cabeçalho
-- do CSV sem manter uma lista de-para no código.
CREATE TABLE erp_dicionario (
  campo  VARCHAR2(30)  NOT NULL,
  nome   VARCHAR2(120),
  tipo   VARCHAR2(20),
  CONSTRAINT pk_erp_dicionario PRIMARY KEY (campo)
);

INSERT INTO erp_dicionario (campo, nome, tipo) VALUES ('B1_COD',     'Codigo',            'C');
INSERT INTO erp_dicionario (campo, nome, tipo) VALUES ('B1_DESC',    'Descricao',         'C');
INSERT INTO erp_dicionario (campo, nome, tipo) VALUES ('B1_TIPO',    'Tipo',              'C');
INSERT INTO erp_dicionario (campo, nome, tipo) VALUES ('B1_UM',      'Unidade de Medida', 'C');
INSERT INTO erp_dicionario (campo, nome, tipo) VALUES ('B1_PRECO',   'Preco de Venda',    'N');
INSERT INTO erp_dicionario (campo, nome, tipo) VALUES ('B1_ESTOQUE', 'Saldo em Estoque',  'N');
INSERT INTO erp_dicionario (campo, nome, tipo) VALUES ('B1_DTCAD',   'Data de Cadastro',  'D');
INSERT INTO erp_dicionario (campo, nome, tipo) VALUES ('B1_ATIVO',   'Ativo',             'N');

INSERT INTO erp_dicionario (campo, nome, tipo) VALUES ('C6_NUM',     'Numero do Pedido',    'C');
INSERT INTO erp_dicionario (campo, nome, tipo) VALUES ('C6_ITEM',    'Item',                'C');
INSERT INTO erp_dicionario (campo, nome, tipo) VALUES ('C6_PRODUTO', 'Produto',             'C');
INSERT INTO erp_dicionario (campo, nome, tipo) VALUES ('C6_QTDVEN',  'Quantidade Vendida',  'N');
INSERT INTO erp_dicionario (campo, nome, tipo) VALUES ('C6_PRCVEN',  'Preco Unitario',      'N');
INSERT INTO erp_dicionario (campo, nome, tipo) VALUES ('C6_VALOR',   'Valor Total',         'N');
INSERT INTO erp_dicionario (campo, nome, tipo) VALUES ('C6_ENTREG',  'Data de Entrega',     'D');
INSERT INTO erp_dicionario (campo, nome, tipo) VALUES ('C6_CLIENTE', 'Cliente',             'C');

COMMIT;

-- A tabela de controle (SYNC_CONTROLE) e as tabelas de estágio (STG_*) são criadas
-- pelo próprio sincronizador na primeira execução.
