"""web/rag_search.py

Recuperação semântica para a busca por tema na UI.
A consulta deixa de ser keyword search e passa a ranquear os itens pelo
acoplamento semântico entre o tema digitado e o conteúdo do projeto.
"""

from __future__ import annotations

import math
import os
import re
from collections import defaultdict
from functools import lru_cache

import openai

from utils.logger import get_logger

logger = get_logger(__name__)

_RAG_EMBEDDING_MODEL = os.getenv("RAG_EMBEDDING_MODEL", "text-embedding-3-small")
_RAG_TOP_K = int(os.getenv("RAG_TOP_K", "30"))


@lru_cache(maxsize=1)
def _get_client() -> openai.OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Busca semântica indisponível: defina OPENAI_API_KEY para usar o modo RAG na interface."
        )

    base_url = os.getenv("OPENAI_BASE_URL") or None
    return openai.OpenAI(api_key=api_key, base_url=base_url)


@lru_cache(maxsize=2048)
def _embed(text: str) -> tuple[float, ...]:
    normalized = " ".join((text or "").split())
    if not normalized:
        return tuple()

    response = _get_client().embeddings.create(
        model=_RAG_EMBEDDING_MODEL,
        input=normalized,
    )
    return tuple(response.data[0].embedding)


def _safe_embed(text: str) -> tuple[float, ...]:
    try:
        return _embed(text)
    except Exception as exc:  # pragma: no cover - fallback de runtime
        logger.warning("RAG: falha ao gerar embedding, usando fallback lexical: %s", exc)
        return tuple()


def _normalize_text(text: str) -> str:
    return " ".join((text or "").split()).lower()


def _tokenize(text: str) -> set[str]:
    return {token for token in re.findall(r"[\wÀ-ÿ]+", _normalize_text(text)) if len(token) > 2}


def _cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0

    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _build_project_text(item: dict) -> str:
    analysis_texts = []
    for analysis in item.get("analysis_order", []):
        text = analysis.get("texto")
        if text:
            analysis_texts.append(text)

    parts = [
        item.get("title", ""),
        item.get("ementa", ""),
        item.get("caption", ""),
        " ".join(analysis_texts),
    ]
    return "\n".join(part for part in parts if part)


def _lexical_score(theme: str, document_text: str) -> float:
    theme_norm = _normalize_text(theme)
    document_norm = _normalize_text(document_text)
    if not theme_norm or not document_norm:
        return 0.0

    theme_tokens = _tokenize(theme_norm)
    if not theme_tokens:
        return 0.0

    document_tokens = _tokenize(document_norm)
    if not document_tokens:
        return 0.0

    overlap = len(theme_tokens & document_tokens) / len(theme_tokens)
    phrase_bonus = 0.15 if theme_norm in document_norm else 0.0
    prefix_bonus = 0.0
    for token in theme_tokens:
        if token in document_norm:
            prefix_bonus += 0.01
    return min(1.0, overlap * 0.72 + phrase_bonus + min(prefix_bonus, 0.12))


def rank_project_groups(groups: dict[str, list[dict]], theme: str) -> dict[str, list[dict]]:
    """Ordena e filtra os projetos de forma semântica usando embeddings.

    Se não houver tema, devolve os grupos inalterados.
    """
    if not theme.strip():
        return groups

    theme_norm = _normalize_text(theme)
    theme_vector = _safe_embed(theme_norm)

    scored: list[tuple[float, str, dict]] = []
    for group_label, items in groups.items():
        for item in items:
            document_text = _build_project_text(item)
            lexical_score = _lexical_score(theme_norm, document_text)
            document_vector = _safe_embed(document_text) if theme_vector else tuple()
            semantic_score = _cosine_similarity(theme_vector, document_vector) if theme_vector and document_vector else 0.0
            combined_score = round(min(1.0, (semantic_score * 0.78) + (lexical_score * 0.22)), 6)
            ranked_item = dict(item)
            ranked_item["semantic_score"] = round(semantic_score, 6)
            ranked_item["lexical_score"] = round(lexical_score, 6)
            ranked_item["rank_score"] = combined_score
            scored.append((combined_score, group_label, ranked_item))

    scored.sort(key=lambda entry: entry[0], reverse=True)

    if not scored or all(score <= 0 for score, _, _ in scored):
        logger.info("RAG: nenhum ganho semântico/lexical relevante para o tema informado.")
        return groups

    ranked_groups: dict[str, list[dict]] = defaultdict(list)
    for score, group_label, item in scored[:_RAG_TOP_K]:
        ranked_groups[group_label].append(item)

    if not ranked_groups:
        logger.info("RAG: nenhum item retornado para o tema informado.")
        return {}

    return dict(ranked_groups)
