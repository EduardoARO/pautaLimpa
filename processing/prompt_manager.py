"""
processing/prompt_manager.py
Épico 2 — Gerenciador de System Prompt versionado.

Responsabilidades:
  - Carregar a versão ATIVA do System Prompt do banco (tabela historico_prompt)
  - Montar o prompt de usuário com os dados do projeto
  - Fornecer o ID da versão usada para rastreabilidade (Épico 6)
"""

from sqlalchemy import text

from models.database import get_session
from utils.logger import get_logger

logger = get_logger(__name__)

_FETCH_PROMPT_SQL = text("""
    SELECT id, versao, system_prompt
    FROM historico_prompt
    WHERE ativo = TRUE
    LIMIT 1
""")

# Prompt de usuário: injeta os metadados e texto do projeto
_USER_PROMPT_TEMPLATE = """
Analise o projeto de lei a seguir e gere o texto para publicação no Instagram seguindo ESTRITAMENTE as 4 regras das suas instruções.

DADOS DO PROJETO:
Tipo: {sigla_tipo}
Número: {numero}
Ano: {ano}
Ementa Original: {ementa}

TEXTO PARA ANÁLISE:
{texto_limpo}

Lembre-se: a PRIMEIRA LINHA da resposta DEVE ser: {sigla_tipo} - {numero}/{ano}
""".strip()


class PromptManager:
    """
    Carrega o System Prompt ativo do banco e monta os prompts para a LLM.
    O cache em memória evita queries repetidas ao banco durante um mesmo run.
    """

    def __init__(self) -> None:
        self._cached_prompt: dict | None = None

    def _load_active_prompt(self) -> dict:
        """Busca a versão ativa do System Prompt no banco."""
        with get_session() as session:
            result = session.execute(_FETCH_PROMPT_SQL).fetchone()

        if not result:
            raise RuntimeError(
                "Nenhum System Prompt ativo encontrado na tabela historico_prompt. "
                "Execute o schema.sql para inserir o prompt v1.0.0."
            )

        return {"id": result.id, "versao": result.versao, "system_prompt": result.system_prompt}

    def get_active_prompt(self) -> dict:
        """
        Retorna o prompt ativo com cache em memória.

        Returns:
            dict: {"id": int, "versao": str, "system_prompt": str}
        """
        if self._cached_prompt is None:
            self._cached_prompt = self._load_active_prompt()
            logger.info("System Prompt carregado: versão %s (id=%d)", self._cached_prompt["versao"], self._cached_prompt["id"])
        return self._cached_prompt

    def build_messages(self, projeto: dict) -> tuple[list[dict], int]:
        """
        Monta a lista de mensagens no formato OpenAI Chat Completions.

        Args:
            projeto: Dict com keys: sigla_tipo, numero, ano, ementa_bruta, texto_limpo

        Returns:
            tuple:
              - list[dict]: Messages no formato [{"role": "system", ...}, {"role": "user", ...}]
              - int: ID da versão do prompt (para gravar em processamento_ia.fk_versao_prompt)
        """
        prompt_data = self.get_active_prompt()

        user_content = _USER_PROMPT_TEMPLATE.format(
            sigla_tipo=projeto.get("sigla_tipo", "PL"),
            numero=projeto.get("numero", "???"),
            ano=projeto.get("ano", "????"),
            ementa=projeto.get("ementa_bruta", ""),
            texto_limpo=projeto.get("texto_limpo", projeto.get("ementa_bruta", "")),
        )

        messages = [
            {"role": "system", "content": prompt_data["system_prompt"]},
            {"role": "user",   "content": user_content},
        ]

        return messages, prompt_data["id"]

    def invalidate_cache(self) -> None:
        """Força recarga do prompt na próxima chamada (usar após atualizar o banco)."""
        self._cached_prompt = None
        logger.info("Cache do System Prompt invalidado.")
