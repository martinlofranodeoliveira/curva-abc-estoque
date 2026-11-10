"""Erros públicos com códigos estáveis e mensagens em português sem expor dados brutos."""
from __future__ import annotations


class EstoqueError(ValueError):
    """Erro de validação, domínio ou entrada/saída do pacote curva-abc-estoque."""

    def __init__(self, code: str, message: str, path: str = "$") -> None:
        self.code = code
        self.message = message
        self.path = path
        super().__init__(f"[{code}] {message} (caminho: {path})")
