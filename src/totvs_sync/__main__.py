"""Permite ``python -m totvs_sync`` além do script ``totvs-sync``."""

from .cli import main

raise SystemExit(main())
