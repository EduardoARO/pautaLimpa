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
        try:
            import anthropic
            self._anthropic = anthropic
            self.client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
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

    def _call_with_retry(self, provider, messages: list[dict]) -> dict | None:
        """
        Chama um provedor com retry automático para 429 e 5xx.

        Returns:
            dict com resultado ou None se todos os retries falharem.
        """
        for attempt in range(1, _MAX_PRIMARY_ATTEMPTS + 1):
            try:
                return provider.complete(messages)
            except openai.RateLimitError as exc:
                retry_wait = _RETRY_WAIT_SECONDS
                match = _RETRY_DELAY_PATTERN.search(str(exc))
                if match:
                    retry_wait = int(float(match.group(1) or match.group(2))) + 1
                logger.warning(
                    "Rate limit (429) no provedor %s | tentativa %d/%d | aguardando %ds.",
                    provider.__class__.__name__, attempt, _MAX_PRIMARY_ATTEMPTS, retry_wait,
                )
                time.sleep(retry_wait)
            except (openai.APIStatusError, Exception) as exc:
                if hasattr(exc, "status_code") and getattr(exc, "status_code", 0) >= 500:
                    logger.warning(
                        "Erro servidor no provedor %s [%s] | tentativa %d/%d | aguardando %ds.",
                        provider.__class__.__name__, exc, attempt, _MAX_PRIMARY_ATTEMPTS, _RETRY_WAIT_SECONDS,
                    )
                    time.sleep(_RETRY_WAIT_SECONDS)
                else:
                    logger.error("Erro inesperado no provedor %s: %s", provider.__class__.__name__, exc)
                    return None
        return None

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
        resultado = self._call_with_retry(self._primary, messages)
        status_ia = "SUCESSO"
        is_fallback = False

        if resultado is None:
            logger.warning(
                "Provedor primário esgotado para id=%d — acionando fallback.", fk_projeto
            )
            if self._fallback is None:
                try:
                    self._fallback = AnthropicProvider()
                except ImportError:
                    logger.error("Fallback indisponível (anthropic não instalado).")
                    self._save_result(
                        fk_projeto, fk_versao_prompt, texto_para_llm,
                        None, "ERRO_LLM", processado_parcialmente, "QUARENTENA",
                    )
                    return "ERRO_LLM"

            resultado = self._call_with_retry(self._fallback, messages)
            is_fallback = True

        if resultado is None:
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

        # Trunca resposta ao limite do Instagram (2.200 chars)
        texto_final = resultado["text"]
        if len(texto_final) > _MAX_RESPONSE_CHARS:
            texto_final = texto_final[:_MAX_RESPONSE_CHARS]
            resultado["text"] = texto_final
            logger.warning(
                "Resposta da LLM truncada para %d chars (limite Instagram) | id=%d.",
                _MAX_RESPONSE_CHARS, fk_projeto,
            )

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
            dict: {"processados": N, "sucesso": N, "quarentena": N, "erro": N}
        """
        logger.info("Iniciando processamento LLM (batch_size=%d)...", batch_size)
        stats = {"processados": 0, "sucesso": 0, "quarentena": 0, "erro": 0}

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

        logger.info(
            "Processamento LLM concluído | processados=%d | sucesso=%d | quarentena=%d | erro=%d",
            stats["processados"], stats["sucesso"], stats["quarentena"], stats["erro"],
        )
        return stats


if __name__ == "__main__":
    from models.database import check_connection
    if not check_connection():
        raise SystemExit(1)
    processor = LLMProcessor()
    processor.run(batch_size=int(os.getenv("LLM_BATCH_SIZE", "10")))
