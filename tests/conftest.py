"""Configuração comum dos testes.

Carrega o ``.env`` da raiz do projeto **no import**, antes de os módulos de teste
serem coletados: o ``skipif`` da integração é avaliado nesse momento, então as
variáveis precisam já estar no ambiente.

Vale por si o motivo de não usar ``source .env`` no shell: senha com ``&``, ``$``
ou espaço é interpretada pelo bash e chega errada (ou nem chega). O carregador do
pacote lê o arquivo como dado, não como script.
"""

from __future__ import annotations

from pathlib import Path

from totvs_sync.ambiente import carregar_dotenv

carregar_dotenv(Path(__file__).resolve().parents[1] / ".env")
