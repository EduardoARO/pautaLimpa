"""
processing/chunker.py
Épico 2 — Estratégia de Chunking para textos legislativos longos.

Responsabilidades:
  - Estimar tokens do texto antes do envio à LLM
  - Truncar textos que ultrapassam o limite seguro do context window
  - Logar aviso quando processamento parcial for necessário

Estimativa de tokens: ~4 caracteres por token (padrão OpenAI para português).
Margem de segurança de 10% aplicada automaticamente.
"""

import os
import re

from utils.logger import get_logger

logger = get_logger(__name__)

# Limite seguro de tokens de entrada (conservador em relação ao max do modelo)
_DEFAULT_TOKEN_LIMIT = int(os.getenv("LLM_TOKEN_INPUT_LIMIT", "8000"))

# Caracteres por token — média para português (~4.0; inglês ~4.5)
_CHARS_PER_TOKEN = 4.0

# Marcadores textuais de seções legislativas para truncamento inteligente
_RE_ARTIGO = re.compile(r"\bArt\.\s*\d+", re.IGNORECASE)


def count_tokens(text: str) -> int:
    """
    Estima o número de tokens de um texto.

    Usa a heurística de 4 chars/token com margem de segurança de 10%,
    adequada para textos em português sem dependência de compiladores Rust.

    Args:
        text: Texto a ser estimado.

    Returns:
        int: Número de tokens estimados (com margem de 10%).
    """
    raw_estimate = len(text) / _CHARS_PER_TOKEN
    return int(raw_estimate * 1.10)  # margem de segurança de 10%


def _extract_priority_sections(text: str) -> list[str]:
    """
    Extrai seções de maior importância de um texto legislativo longo.

    Estratégia de priorização (conforme Épico 2 — critério de aceite):
      1. Ementa completa (primeiros 500 chars, geralmente é a ementa)
      2. Justificativa (se detectada por marcador textual)
      3. Primeiros 3 artigos (Art. 1, Art. 2, Art. 3)

    Args:
        text: Texto limpo completo do projeto de lei.

    Returns:
        list[str]: Lista ordenada das seções extraídas.
    """
    sections: list[str] = []

    # Seção 1: Ementa (assume que os primeiros ~2 parágrafos são a ementa)
    paragrafos = [p.strip() for p in text.split("\n\n") if p.strip()]
    if paragrafos:
        sections.append(paragrafos[0])

    # Seção 2: Justificativa (busca por marcador textual explícito)
    justificativa_match = re.search(
        r"(justificativa|justificação|exposição de motivos)(.*?)(?=art\.\s*1|\Z)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if justificativa_match:
        justificativa = justificativa_match.group(0)[:2000]
        sections.append(justificativa)

    # Seção 3: Primeiros 3 artigos
    artigos = _RE_ARTIGO.split(text)
    for i, artigo in enumerate(artigos[1:4], start=1):
        sections.append(f"Art. {i}{artigo[:500]}")

    return sections


def prepare_text_for_llm(text: str, token_limit: int = _DEFAULT_TOKEN_LIMIT) -> tuple[str, bool]:
    """
    Prepara o texto para envio à LLM, aplicando truncamento inteligente se necessário.

    Args:
        text:        Texto limpo completo do projeto.
        token_limit: Limite máximo de tokens de entrada.

    Returns:
        tuple[str, bool]:
          - Texto final (completo ou truncado)
          - bool: True se o texto foi truncado (processado_parcialmente)
    """
    token_count = count_tokens(text)

    if token_count <= token_limit:
        logger.debug("Texto dentro do limite (%d tokens) — envio completo.", token_count)
        return text, False

    # Texto excede o limite — aplica truncamento inteligente por seções
    logger.warning(
        "Texto com %d tokens excede limite de %d — aplicando truncamento inteligente.",
        token_count,
        token_limit,
    )

    sections = _extract_priority_sections(text)
    truncated = "\n\n".join(sections)

    # Garante que o texto truncado também esteja dentro do limite
    while count_tokens(truncated) > token_limit and sections:
        sections.pop()
        truncated = "\n\n".join(sections)

    truncated_tokens = count_tokens(truncated)
    logger.warning(
        "Texto truncado para %d tokens (era %d). "
        "Processamento parcial: ementa + justificativa + 3 artigos.",
        truncated_tokens,
        token_count,
    )

    return truncated, True
