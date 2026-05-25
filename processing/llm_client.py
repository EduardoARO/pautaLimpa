"""
processing/llm_client.py
Épico 2 — Cliente LLM com fallback e retry.

Fluxo de resiliência:
  - Primário:    OpenAI GPT-4o
  - Fallback:    Anthropic Claude 3 Haiku (acionado após 3 falhas consecutivas)
  - Retry:       429 (rate limit) e 5xx → aguarda 30s, tenta novamente
  - Após 3 falhas no primário: chaveia automaticamente para o fallback
  - Resposta gravada em processamento_ia com tokens_usados, modelo e status
"""

import os
import re
import time

import openai
import requests
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from models.database import get_session
from processing.chunker import prepare_text_for_llm, count_tokens
from processing.prompt_manager import PromptManager
from utils.logger import get_logger

load_dotenv()

logger = get_logger(__name__)

# Tentativas no provedor primário antes de acionar fallback
_MAX_PRIMARY_ATTEMPTS = int(os.getenv("LLM_MAX_PRIMARY_ATTEMPTS", "3"))
# Espera em segundos entre retries de 429/5xx
_RETRY_WAIT_SECONDS   = int(os.getenv("LLM_RETRY_WAIT_SECONDS", "30"))
# Limite de caracteres da resposta (Instagram)
_MAX_RESPONSE_CHARS   = 2200
# Tamanho minimo do texto explicativo apos a linha de citacao
_MIN_BODY_CHARS       = 300

_REQUIRED_CITATION_PATTERN = re.compile(r"^[A-Z]{2,10}\s*-\s*\d{1,6}/\d{4}$")

# Frases que indicam recusa do modelo (detectar RECUSA_MODELO)
_REFUSAL_PATTERNS = [
    "desculpe, não posso",
    "sorry, i cannot",
    "não é possível analisar",
    "não consigo processar",
    "como modelo de linguagem",
    "não tenho capacidade",
]

_RETRY_DELAY_PATTERN = re.compile(
    r"retry in\s+([0-9]+(?:\.[0-9]+)?)s|retryDelay':\s*'([0-9]+)s",
    re.IGNORECASE,
)

# SQLs de persistência
_NON_RETRYABLE_QUOTA_MARKERS = (
    "insufficient_quota",
    "quota exceeded",
    "current quota",
    "generaterequestsperdayperprojectpermodel-freetier",
    "generativelanguage.googleapis.com/generate_content_free_tier_requests",
)

_AUTH_ERROR_MARKERS = (
    "could not resolve authentication method",
    "api key",
    "auth_token",
    "authorization",
    "credentials",
    "authentication",
)

_DEFERRED_PROVIDER_FAILURES = {
    "QUOTA_EXCEEDED",
    "AUTH_ERROR",
}

_UPSERT_IA_SQL = text("""
    INSERT INTO processamento_ia (
        fk_projeto, fk_versao_prompt, texto_limpo,
        texto_traduzido, status_ia,
        prompt_tokens, completion_tokens,
        modelo_llm, processado_parcialmente
    ) VALUES (
        :fk_projeto, :fk_versao_prompt, :texto_limpo,
        :texto_traduzido, :status_ia,
        :prompt_tokens, :completion_tokens,
        :modelo_llm, :processado_parcialmente
    )
    ON CONFLICT ON CONSTRAINT uq_processamento_projeto
    DO UPDATE SET
        fk_versao_prompt       = EXCLUDED.fk_versao_prompt,
        texto_limpo            = EXCLUDED.texto_limpo,
        texto_traduzido        = EXCLUDED.texto_traduzido,
        status_ia              = EXCLUDED.status_ia,
        prompt_tokens          = EXCLUDED.prompt_tokens,
        completion_tokens      = EXCLUDED.completion_tokens,
        modelo_llm             = EXCLUDED.modelo_llm,
        processado_parcialmente = EXCLUDED.processado_parcialmente,
        data_processamento     = NOW()
""")

_UPDATE_PROJETO_STATUS_SQL = text("""
    UPDATE projetos_brutos
    SET status_processamento = :status
    WHERE id = :id
""")

_FETCH_QUEUE_SQL = text("""
    SELECT id, id_origem, sigla_tipo, numero, ano, ementa_bruta
    FROM projetos_brutos
    WHERE status_processamento = 'AGUARDANDO_IA'
    ORDER BY data_captura ASC
    LIMIT :limit
""")


class OpenAIProvider:
    """Provedor primário: OpenAI GPT-4o ou qualquer provedor OpenAI-compatível (Kimi, Groq, etc.)."""

    def __init__(self) -> None:
        base_url = os.getenv("OPENAI_BASE_URL") or None  # None = padrão OpenAI
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.model  = os.getenv("OPENAI_MODEL", "gpt-4o")
        self.base_url = base_url
        self.is_gemini_native = bool(base_url and "generativelanguage.googleapis.com" in base_url)
        self.client = openai.OpenAI(
            api_key=self.api_key,
            base_url=base_url,
        )
        self.max_tokens = int(os.getenv("OPENAI_MAX_TOKENS", "1024"))
        self.temperature = float(os.getenv("OPENAI_TEMPERATURE", "0.3"))

    def complete(self, messages: list[dict]) -> dict:
        """
        Chama a API do OpenAI e retorna resultado padronizado.

        Returns:
            dict: {"text": str, "prompt_tokens": int, "completion_tokens": int, "model": str}

        Raises:
            openai.RateLimitError: Em caso de 429.
            openai.APIStatusError: Em caso de 5xx.
        """
        if self.is_gemini_native:
            return self._complete_gemini_native(messages)

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        return {
            "text":              response.choices[0].message.content or "",
            "prompt_tokens":     response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "model":             response.model,
        }

    def _complete_gemini_native(self, messages: list[dict]) -> dict:
        """Chama a API nativa do Gemini via generateContent."""
        system_text = "\n\n".join(
            message["content"] for message in messages if message.get("role") == "system"
        )
        user_text = "\n\n".join(
            message["content"] for message in messages if message.get("role") == "user"
        )

        prompt = f"{system_text}\n\n{user_text}".strip()
        url = (
            "https://generativelanguage.googleapis.com/v1beta/"
            f"models/{self.model}:generateContent"
        )
        response = requests.post(
            url,
            headers={
                "Content-Type": "application/json",
                "X-goog-api-key": self.api_key or "",
            },
            json={
                "contents": [
                    {
                        "parts": [
                            {"text": prompt}
                        ]
                    }
                ],
                "generationConfig": {
                    "temperature": self.temperature,
                    "maxOutputTokens": self.max_tokens,
                },
            },
            timeout=60,
        )

        if response.status_code == 429:
            raise openai.RateLimitError(
                message=response.text,
                response=response,
                body=response.json(),
            )
        response.raise_for_status()
        data = response.json()
        text = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        )
        usage = data.get("usageMetadata", {})
        return {
            "text":              text,
            "prompt_tokens":     usage.get("promptTokenCount", count_tokens(prompt)),
            "completion_tokens": usage.get("candidatesTokenCount", count_tokens(text)),
            "model":             self.model,
        }


class AnthropicProvider:
    """Provedor de fallback: Anthropic Claude 3 Haiku."""

    def __init__(self) -> None:
        api_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY nao configurada.")

        try:
            import anthropic
            self._anthropic = anthropic
            self.client = anthropic.Anthropic(api_key=api_key)
        except ImportError:
            raise ImportError(
                "Pacote 'anthropic' não instalado. "
                "Execute: pip install anthropic"
            )
        self.model      = os.getenv("ANTHROPIC_MODEL", "claude-3-haiku-20240307")
        self.max_tokens = int(os.getenv("ANTHROPIC_MAX_TOKENS", "1024"))

    def complete(self, messages: list[dict]) -> dict:
        """
        Adapta o formato OpenAI para o formato Anthropic e retorna padronizado.
        Separa system message do array de mensagens (requisito da API Anthropic).
        """
        system_msg = ""
        user_msgs  = []
        for m in messages:
            if m["role"] == "system":
                system_msg = m["content"]
            else:
                user_msgs.append(m)

        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system_msg,
            messages=user_msgs,
        )
        return {
            "text":              response.content[0].text if response.content else "",
            "prompt_tokens":     response.usage.input_tokens,
            "completion_tokens": response.usage.output_tokens,
            "model":             self.model,
        }


class LLMProcessor:
    """
    Orquestra o processamento LLM de projetos com status AGUARDANDO_IA.

    Lógica de resiliência:
      1. Tenta OpenAI até _MAX_PRIMARY_ATTEMPTS vezes (retry 429/5xx com 30s de espera)
      2. Após 3 falhas → aciona AnthropicProvider (fallback)
      3. Se fallback também falha → status ERRO_LLM
      4. Detecção de recusa do modelo → status RECUSA_MODELO → QUARENTENA
      5. Todos os resultados gravados em processamento_ia + atualiza projetos_brutos
    """

    def __init__(
        self,
        prompt_manager: PromptManager | None = None,
        primary:        OpenAIProvider | None = None,
        fallback:       AnthropicProvider | None = None,
    ) -> None:
        self._prompt_manager = prompt_manager or PromptManager()
        self._primary  = primary  or OpenAIProvider()
        self._fallback = fallback

    def _is_refusal(self, text: str) -> bool:
        """Detecta se a resposta da LLM é uma recusa de processamento."""
        text_lower = text.lower()
        return any(pattern in text_lower for pattern in _REFUSAL_PATTERNS)

    def _error_text(self, exc: Exception) -> str:
        """Normaliza a mensagem da excecao para inspecao."""
        parts = [str(exc)]

        body = getattr(exc, "body", None)
        if body:
            parts.append(str(body))

        response = getattr(exc, "response", None)
        if response is not None:
            response_text = getattr(response, "text", None)
            if response_text:
                parts.append(response_text)

        return " ".join(part for part in parts if part)

    def _is_non_retryable_rate_limit(self, exc: Exception) -> bool:
        """Detecta cotas esgotadas em que esperar nao ajuda."""
        error_text = self._error_text(exc).lower()
        return any(marker in error_text for marker in _NON_RETRYABLE_QUOTA_MARKERS)

    def _is_auth_error(self, exc: Exception) -> bool:
        """Detecta erro de autenticacao/configuracao do provedor."""
        status_code = getattr(exc, "status_code", None)
        if status_code in (401, 403):
            return True

        error_text = self._error_text(exc).lower()
        return any(marker in error_text for marker in _AUTH_ERROR_MARKERS)

    def _should_defer_processing(self, reason: str | None) -> bool:
        """Decide se a falha deve manter o item na fila para nova tentativa futura."""
        return reason in _DEFERRED_PROVIDER_FAILURES

    def _call_with_retry(self, provider, messages: list[dict]) -> tuple[dict | None, str | None]:
        """
        Chama um provedor com retry automático para 429 e 5xx.

        Returns:
            Tupla (resultado, motivo_falha).
        """
        failure_reason = None
        for attempt in range(1, _MAX_PRIMARY_ATTEMPTS + 1):
            try:
                return provider.complete(messages), None
            except openai.RateLimitError as exc:
                if self._is_non_retryable_rate_limit(exc):
                    logger.warning(
                        "Quota esgotada no provedor %s; retries ignorados e fallback liberado.",
                        provider.__class__.__name__,
                    )
                    return None, "QUOTA_EXCEEDED"

                retry_wait = _RETRY_WAIT_SECONDS
                match = _RETRY_DELAY_PATTERN.search(str(exc))
                if match:
                    retry_wait = int(float(match.group(1) or match.group(2))) + 1
                logger.warning(
                    "Rate limit (429) no provedor %s | tentativa %d/%d | aguardando %ds.",
                    provider.__class__.__name__, attempt, _MAX_PRIMARY_ATTEMPTS, retry_wait,
                )
                failure_reason = "RATE_LIMIT_RETRY_EXHAUSTED"
                time.sleep(retry_wait)
            except (openai.APIStatusError, Exception) as exc:
                if hasattr(exc, "status_code") and getattr(exc, "status_code", 0) >= 500:
                    logger.warning(
                        "Erro servidor no provedor %s [%s] | tentativa %d/%d | aguardando %ds.",
                        provider.__class__.__name__, exc, attempt, _MAX_PRIMARY_ATTEMPTS, _RETRY_WAIT_SECONDS,
                    )
                    failure_reason = "SERVER_ERROR"
                    time.sleep(_RETRY_WAIT_SECONDS)
                elif self._is_auth_error(exc):
                    logger.error(
                        "Erro de autenticacao/configuracao no provedor %s: %s",
                        provider.__class__.__name__, exc,
                    )
                    return None, "AUTH_ERROR"
                else:
                    logger.error("Erro inesperado no provedor %s: %s", provider.__class__.__name__, exc)
                    return None, "UNEXPECTED_ERROR"
        return None, failure_reason or "RETRY_EXHAUSTED"

    def _save_result(
        self,
        fk_projeto:              int,
        fk_versao_prompt:        int,
        texto_limpo:             str,
        resultado:               dict | None,
        status_ia:               str,
        processado_parcialmente: bool,
        novo_status_projeto:     str,
    ) -> None:
        """Persiste resultado em processamento_ia e atualiza projetos_brutos."""
        payload = {
            "fk_projeto":              fk_projeto,
            "fk_versao_prompt":        fk_versao_prompt,
            "texto_limpo":             texto_limpo,
            "texto_traduzido":         resultado.get("text")             if resultado else None,
            "status_ia":               status_ia,
            "prompt_tokens":           resultado.get("prompt_tokens")    if resultado else None,
            "completion_tokens":       resultado.get("completion_tokens") if resultado else None,
            "modelo_llm":              resultado.get("model")            if resultado else None,
            "processado_parcialmente": processado_parcialmente,
        }
        with get_session() as session:
            try:
                session.execute(_UPSERT_IA_SQL, payload)
                session.execute(
                    _UPDATE_PROJETO_STATUS_SQL,
                    {"status": novo_status_projeto, "id": fk_projeto},
                )
                session.commit()
            except SQLAlchemyError as exc:
                session.rollback()
                logger.error("Erro ao salvar resultado IA para fk_projeto=%d: %s", fk_projeto, exc)
                raise

    def _truncate_to_instagram_limit(self, text: str) -> str:
        """Garante o limite absoluto de caracteres aceito na legenda."""
        if len(text) <= _MAX_RESPONSE_CHARS:
            return text
        return text[:_MAX_RESPONSE_CHARS].rstrip()

    def _validate_generated_text(self, text: str) -> str | None:
        """Valida regras estruturais da saida da LLM."""
        normalized = (text or "").strip()
        if not normalized:
            return "a resposta veio vazia"

        if len(normalized) > _MAX_RESPONSE_CHARS:
            return f"a resposta ultrapassou {_MAX_RESPONSE_CHARS} caracteres"

        lines = normalized.splitlines()
        first_line = lines[0].strip() if lines else ""
        body = "\n".join(lines[1:]).strip()

        if not _REQUIRED_CITATION_PATTERN.fullmatch(first_line):
            return "a primeira linha nao esta no formato [TIPO] - [NUMERO]/[ANO]"

        if len(body) < _MIN_BODY_CHARS:
            return f"o texto explicativo ficou com menos de {_MIN_BODY_CHARS} caracteres"

        return None

    def _build_correction_messages(
        self,
        base_messages: list[dict],
        invalid_text: str,
        invalid_reason: str,
        projeto_row: dict,
    ) -> list[dict]:
        """Pede ao modelo uma reescrita quando a primeira saida viola regras formais."""
        correction_request = (
            "Sua resposta anterior foi rejeitada porque "
            f"{invalid_reason}. Reescreva do zero e cumpra todas as 5 regras. "
            f"A primeira linha deve ser exatamente {projeto_row['sigla_tipo']} - "
            f"{projeto_row['numero']}/{projeto_row['ano']}. "
            f"O texto explicativo apos a primeira linha deve ter no minimo {_MIN_BODY_CHARS} caracteres "
            f"e a resposta completa nunca pode ultrapassar {_MAX_RESPONSE_CHARS} caracteres. "
            "Mantenha tom estritamente analitico, jornalistico e sem adjetivos opinativos."
        )
        return base_messages + [
            {"role": "assistant", "content": invalid_text},
            {"role": "user", "content": correction_request},
        ]

    def process_one(self, projeto_row: dict) -> str:
        """
        Processa um único projeto.

        Args:
            projeto_row: Dict com id, id_origem, sigla_tipo, numero, ano, ementa_bruta

        Returns:
            str: Status final da IA (SUCESSO, FALLBACK_UTILIZADO, ERRO_LLM, RECUSA_MODELO)
        """
        fk_projeto = projeto_row["id"]
        logger.info(
            "Processando LLM | id=%d | %s %s/%s",
            fk_projeto, projeto_row["sigla_tipo"],
            projeto_row["numero"], projeto_row["ano"],
        )

        # Prepara texto com chunking (trunca se necessário)
        texto_para_llm, processado_parcialmente = prepare_text_for_llm(
            projeto_row.get("ementa_bruta", "")
        )

        # Monta mensagens e obtém ID da versão do prompt
        projeto_row["texto_limpo"] = texto_para_llm
        messages, fk_versao_prompt = self._prompt_manager.build_messages(projeto_row)

        # Tentativa no provedor PRIMÁRIO
        resultado, primary_failure = self._call_with_retry(self._primary, messages)
        status_ia = "SUCESSO"
        is_fallback = False

        if resultado is None:
            logger.warning(
                "Provedor primário esgotado para id=%d — acionando fallback.", fk_projeto
            )
            if self._fallback is None:
                try:
                    self._fallback = AnthropicProvider()
                except (ImportError, ValueError) as exc:
                    logger.error("Fallback indisponivel: %s", exc)
                    logger.warning(
                        "Nenhum provedor disponivel para id=%d; item mantido em AGUARDANDO_IA.",
                        fk_projeto,
                    )
                    return "ADIADO_SEM_PROVEDOR"

            resultado, fallback_failure = self._call_with_retry(self._fallback, messages)
            is_fallback = True
        else:
            fallback_failure = None

        if resultado is None:
            if (
                self._should_defer_processing(primary_failure)
                or self._should_defer_processing(fallback_failure)
            ):
                logger.warning(
                    "Provedores indisponiveis para id=%d; item mantido em AGUARDANDO_IA.",
                    fk_projeto,
                )
                return "ADIADO_SEM_PROVEDOR"

            logger.error("Todos os provedores falharam para id=%d.", fk_projeto)
            self._save_result(
                fk_projeto, fk_versao_prompt, texto_para_llm,
                None, "ERRO_LLM", processado_parcialmente, "QUARENTENA",
            )
            return "ERRO_LLM"

        # Detecta recusa do modelo
        if self._is_refusal(resultado["text"]):
            logger.warning("Recusa detectada na resposta da LLM para id=%d.", fk_projeto)
            self._save_result(
                fk_projeto, fk_versao_prompt, texto_para_llm,
                resultado, "RECUSA_MODELO", processado_parcialmente, "QUARENTENA",
            )
            return "RECUSA_MODELO"

        # Garante limite do Instagram e tenta uma autocorrecao se a saida vier fora das regras.
        texto_final = self._truncate_to_instagram_limit(resultado["text"] or "")
        if texto_final != (resultado["text"] or ""):
            resultado["text"] = texto_final
            logger.warning(
                "Resposta da LLM truncada para %d chars (limite Instagram) | id=%d.",
                _MAX_RESPONSE_CHARS, fk_projeto,
            )

        invalid_reason = self._validate_generated_text(texto_final)
        if invalid_reason:
            logger.warning(
                "Saida inicial invalida para id=%d (%s). Solicitando reescrita ao modelo.",
                fk_projeto, invalid_reason,
            )
            provider_used = self._fallback if is_fallback else self._primary
            correction_messages = self._build_correction_messages(
                messages, texto_final, invalid_reason, projeto_row,
            )
            corrected_result, _ = self._call_with_retry(provider_used, correction_messages)

            if corrected_result is not None and not self._is_refusal(corrected_result["text"]):
                corrected_text = self._truncate_to_instagram_limit(corrected_result["text"] or "")
                if corrected_text != (corrected_result["text"] or ""):
                    logger.warning(
                        "Resposta corrigida da LLM truncada para %d chars | id=%d.",
                        _MAX_RESPONSE_CHARS, fk_projeto,
                    )
                corrected_result["text"] = corrected_text
                corrected_invalid_reason = self._validate_generated_text(corrected_text)
                if corrected_invalid_reason is None:
                    resultado = corrected_result
                    texto_final = corrected_text
                    logger.info("Saida corrigida com sucesso para id=%d.", fk_projeto)
                else:
                    resultado = corrected_result
                    texto_final = corrected_text
                    logger.warning(
                        "Saida corrigida ainda invalida para id=%d (%s). Quarentena validara o item.",
                        fk_projeto, corrected_invalid_reason,
                    )
            else:
                logger.warning(
                    "Nao foi possivel autocorrigir a saida para id=%d; quarentena validara o item.",
                    fk_projeto,
                )

        resultado["text"] = texto_final

        status_ia = "FALLBACK_UTILIZADO" if is_fallback else "SUCESSO"
        self._save_result(
            fk_projeto, fk_versao_prompt, texto_para_llm,
            resultado, status_ia, processado_parcialmente, "AGUARDANDO_MIDIA",
        )

        total_tokens = (resultado.get("prompt_tokens") or 0) + (resultado.get("completion_tokens") or 0)
        logger.info(
            "LLM OK | id=%d | modelo=%s | tokens=%d | status=%s",
            fk_projeto, resultado.get("model"), total_tokens, status_ia,
        )
        return status_ia

    def run(self, batch_size: int = 10) -> dict[str, int]:
        """
        Processa todos os projetos com status AGUARDANDO_IA em lotes.

        Args:
            batch_size: Máximo de projetos por execução.

        Returns:
            dict: {"processados": N, "sucesso": N, "quarentena": N, "erro": N, "adiados": N}
        """
        logger.info("Iniciando processamento LLM (batch_size=%d)...", batch_size)
        stats = {"processados": 0, "sucesso": 0, "quarentena": 0, "erro": 0, "adiados": 0}

        with get_session() as session:
            rows = session.execute(_FETCH_QUEUE_SQL, {"limit": batch_size}).fetchall()

        for row in rows:
            stats["processados"] += 1
            projeto = {
                "id":          row.id,
                "id_origem":   row.id_origem,
                "sigla_tipo":  row.sigla_tipo,
                "numero":      row.numero,
                "ano":         row.ano,
                "ementa_bruta": row.ementa_bruta,
            }
            status = self.process_one(projeto)
            if status in ("SUCESSO", "FALLBACK_UTILIZADO"):
                stats["sucesso"] += 1
            elif status in ("RECUSA_MODELO", "ERRO_LLM"):
                stats["quarentena"] += 1 if status == "RECUSA_MODELO" else 0
                stats["erro"]       += 1 if status == "ERRO_LLM"      else 0
            elif status == "ADIADO_SEM_PROVEDOR":
                stats["adiados"] += 1
                logger.warning(
                    "Lote interrompido por indisponibilidade de provedor; itens restantes permanecem em AGUARDANDO_IA."
                )
                break

        logger.info(
            "Processamento LLM concluído | processados=%d | sucesso=%d | quarentena=%d | erro=%d | adiados=%d",
            stats["processados"], stats["sucesso"], stats["quarentena"], stats["erro"], stats["adiados"],
        )
        return stats


if __name__ == "__main__":
    from models.database import check_connection
    if not check_connection():
        raise SystemExit(1)
    processor = LLMProcessor()
    processor.run(batch_size=int(os.getenv("LLM_BATCH_SIZE", "10")))
