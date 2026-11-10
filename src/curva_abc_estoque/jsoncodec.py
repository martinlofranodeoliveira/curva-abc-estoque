"""Leitura e serialização JSON estritas, sem float, sem BOM e sem chaves duplicadas."""
from __future__ import annotations

import json
import re
from typing import Any

from .errors import EstoqueError

MAX_BYTES = 8_388_608
MAX_DEPTH = 16
MAX_NODES = 250_000
MAX_NUMBER_CHARS = 64
MAX_DIGITS = 40
MAX_OUTPUT_BYTES = 67_108_864
MAX_OUTPUT_NODES = 1_000_000

_INTEGER_TOKEN = re.compile(r"-?(?:0|[1-9][0-9]*)\Z", re.ASCII)


def _error(message: str, code: str = "JSON_INVALID", path: str = "$") -> EstoqueError:
    return EstoqueError(code, message, path)


def _parse_int(token: str) -> int:
    if len(token) > MAX_NUMBER_CHARS or _INTEGER_TOKEN.fullmatch(token) is None:
        raise _error("Inteiro JSON inválido ou com zeros à esquerda.", "JSON_INVALID")
    digits = token.lstrip("-")
    if len(digits) > MAX_DIGITS:
        raise _error("Inteiro JSON excede o limite de dígitos.", "JSON_LIMIT")
    return int(token)


def _reject_float(_token: str) -> Any:
    raise _error(
        "Números em ponto flutuante (float) não são permitidos; use inteiros ou textos racionais.",
        "JSON_FLOAT",
    )


def _reject_constant(_token: str) -> Any:
    raise _error("Constantes não finitas (NaN/Infinity) não são JSON válido.", "JSON_INVALID")


def _pairs_no_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if type(key) is not str:
            raise _error("Chaves de objeto JSON devem ser texto.", "JSON_INVALID")
        if key in result:
            raise _error("Objeto JSON contém chave duplicada.", "JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _validate_string(value: str) -> str:
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
        raise _error("Texto JSON contém código substituto Unicode inválido.", "JSON_INVALID")
    return json.dumps(value, ensure_ascii=False)


def _dumps(value: Any, *, max_nodes: int, max_bytes: int) -> str:
    nodes = 0

    def emit(item: Any, depth: int) -> str:
        nonlocal nodes
        nodes += 1
        if nodes > max_nodes:
            raise _error("Documento JSON excede o limite de nós.", "JSON_LIMIT")
        kind = type(item)
        if kind is dict or kind is list:
            if depth > MAX_DEPTH:
                raise _error("Documento JSON excede o limite de profundidade.", "JSON_LIMIT")
            if not item:
                return "{}" if kind is dict else "[]"
            indent = "  " * depth
            closing_indent = "  " * (depth - 1)
            if kind is dict:
                rows = []
                for key, child in item.items():
                    if type(key) is not str:
                        raise _error("Chaves JSON devem ser texto.", "JSON_INVALID")
                    rows.append(f"{indent}{_validate_string(key)}: {emit(child, depth + 1)}")
                return "{\n" + ",\n".join(rows) + "\n" + closing_indent + "}"
            rows = [f"{indent}{emit(child, depth + 1)}" for child in item]
            return "[\n" + ",\n".join(rows) + "\n" + closing_indent + "]"
        if kind is str:
            return _validate_string(item)
        if item is None:
            return "null"
        if kind is bool:
            return "true" if item else "false"
        if kind is int:
            if abs(item) >= 10**MAX_DIGITS:
                raise _error("Inteiro excede o limite de dígitos na serialização.", "JSON_LIMIT")
            return str(item)
        raise _error("Documento contém tipo Python não suportado na serialização JSON.", "JSON_INVALID")

    text = emit(value, 1) + "\n"
    if len(text.encode("utf-8")) > max_bytes:
        raise _error("Documento JSON excede o limite de bytes serializados.", "JSON_LIMIT")
    return text


def dumps(value: Any) -> str:
    """Serializa estrutura JSON validada dentro dos limites de entrada."""
    return _dumps(value, max_nodes=MAX_NODES, max_bytes=MAX_BYTES)


def dumps_output(value: Any) -> str:
    """Serializa estrutura JSON derivada de relatório com limites de saída."""
    return _dumps(value, max_nodes=MAX_OUTPUT_NODES, max_bytes=MAX_OUTPUT_BYTES)


def loads(data: bytes | str) -> Any:
    """Decodifica UTF-8 estrito sem BOM, recusando floats, NaN/Infinity e chaves repetidas."""
    if type(data) is bytes:
        if len(data) > MAX_BYTES:
            raise _error("Documento excede o limite de bytes.", "JSON_LIMIT")
        if data.startswith(b"\xef\xbb\xbf"):
            raise _error("BOM UTF-8 não é permitido no JSON.", "JSON_BOM")
        try:
            source = data.decode("utf-8", "strict")
        except UnicodeError:
            raise _error("Documento não está em UTF-8 válido.", "JSON_UTF8") from None
    elif type(data) is str:
        try:
            encoded = data.encode("utf-8", "strict")
        except UnicodeError:
            raise _error("Texto contém Unicode inválido.", "JSON_UTF8") from None
        if len(encoded) > MAX_BYTES:
            raise _error("Documento excede o limite de bytes.", "JSON_LIMIT")
        source = data
    else:
        raise _error("A entrada JSON deve ser bytes ou str.", "JSON_INVALID")

    if source.startswith("\ufeff"):
        raise _error("BOM UTF-8 não é permitido no JSON.", "JSON_BOM")

    depth = 0
    quoted = False
    escaped = False
    for ch in source:
        if quoted:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                quoted = False
        elif ch == '"':
            quoted = True
        elif ch in "[{":
            depth += 1
            if depth > MAX_DEPTH:
                raise _error("Documento JSON excede o limite de profundidade.", "JSON_LIMIT")
        elif ch in "]}":
            depth -= 1
            if depth < 0:
                raise _error("Sintaxe JSON inválida.", "JSON_INVALID")

    try:
        parsed = json.loads(
            source,
            parse_int=_parse_int,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
            object_pairs_hook=_pairs_no_duplicates,
        )
    except EstoqueError:
        raise
    except (json.JSONDecodeError, RecursionError, OverflowError):
        raise _error("Sintaxe JSON inválida.", "JSON_INVALID") from None

    dumps(parsed)
    return parsed
