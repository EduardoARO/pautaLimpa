"""
processing/prompt_manager.py
Epico 2 - Gerenciador de System Prompt versionado.

Responsabilidades:
  - Carregar a versao ATIVA do System Prompt do banco (tabela historico_prompt)
  - Montar o prompt de usuario com os dados do projeto
  - Fornecer o ID da versao usada para rastreabilidade (Epico 6)
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

# Prompt de usuario: injeta os metadados e texto do projeto
_USER_PROMPT_TEMPLATE = """
Analise o projeto de lei a seguir e gere o texto para publicacao no Instagram seguindo ESTRITAMENTE as 5 regras das suas instrucoes.

DADOS DO PROJETO:
Tipo: {sigla_tipo}
Numero: {numero}
Ano: {ano}
Ementa Original: {ementa}

TEXTO PARA ANALISE:
{texto_limpo}

Exigencias obrigatorias de saida:
- A PRIMEIRA LINHA da resposta DEVE ser exatamente: {sigla_tipo} - {numero}/{ano}
- O texto explicativo apos a primeira linha deve ter no minimo 300 caracteres
- A resposta completa nunca pode ultrapassar 2.200 caracteres
- Explique o assunto da ementa, a mudanca proposta e quem pode ser afetado
- Priorize explicacao simples e direta, sem repetir a mesma ideia com outras palavras
- Use de preferencia 2 paragrafos curtos: um para explicar o que muda e outro para mostrar o efeito pratico
- Cada paragrafo deve trazer informacao nova, sem reescrever o anterior
- Mantenha tom estritamente analitico, sem adjetivos opinativos
""".strip()


class PromptManager:
    """
    Carrega o System Prompt ativo do banco e monta os prompts para a LLM.
    O cache em memoria evita queries repetidas ao banco durante um mesmo run.
    """

    def __init__(self) -> None:
        self._cached_prompt: dict | None = None

    def _load_active_prompt(self) -> dict:
        """Busca a versao ativa do System Prompt no banco."""
        with get_session() as session:
            result = session.execute(_FETCH_PROMPT_SQL).fetchone()

        if not result:
            raise RuntimeError(
                "Nenhum System Prompt ativo encontrado na tabela historico_prompt. "
                "Execute o schema.sql para inserir o prompt padrao."
            )

        return {"id": result.id, "versao": result.versao, "system_prompt": result.system_prompt}

    def get_active_prompt(self) -> dict:
        """
        Retorna o prompt ativo com cache em memoria.

        Returns:
            dict: {"id": int, "versao": str, "system_prompt": str}
        """
        if self._cached_prompt is None:
            self._cached_prompt = self._load_active_prompt()
            logger.info(
                "System Prompt carregado: versao %s (id=%d)",
                self._cached_prompt["versao"],
                self._cached_prompt["id"],
            )
        return self._cached_prompt

    def build_messages(self, projeto: dict) -> tuple[list[dict], int]:
        """
        Monta a lista de mensagens no formato OpenAI Chat Completions.

        Args:
            projeto: Dict com keys: sigla_tipo, numero, ano, ementa_bruta, texto_limpo

        Returns:
            tuple:
              - list[dict]: Messages no formato [{"role": "system", ...}, {"role": "user", ...}]
              - int: ID da versao do prompt (para gravar em processamento_ia.fk_versao_prompt)
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
            {"role": "user", "content": user_content},
        ]

        return messages, prompt_data["id"]

    def invalidate_cache(self) -> None:
        """Forca recarga do prompt na proxima chamada (usar apos atualizar o banco)."""
        self._cached_prompt = None
        logger.info("Cache do System Prompt invalidado.")
