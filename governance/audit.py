"""
governance/audit.py
Épico 6 — Trilha de Auditoria e Controle de Versão de Prompt.

Responsabilidades:
  - Gravar qualquer intervenção manual na tabela auditoria
  - Aprovar/rejeitar projetos em QUARENTENA com registro auditável
  - Criar nova versão do System Prompt (desativa a anterior)
  - Consultar histórico de auditoria de um projeto
"""

import json
import os

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from models.database import get_session
from utils.logger import get_logger

load_dotenv()

logger = get_logger(__name__)

# Usuário padrão para ações automáticas (substitui em intervenções manuais)
_SYSTEM_USER = os.getenv("AUDIT_SYSTEM_USER", "pipeline_automatico")

_INSERT_AUDIT_SQL = text("""
    INSERT INTO auditoria (entidade, id_entidade, acao, usuario, dados_antes, dados_depois, observacao)
    VALUES (:entidade, :id_entidade, :acao, :usuario, :dados_antes, :dados_depois, :observacao)
""")

_FETCH_PROJETO_SQL = text("""
    SELECT id, status_processamento, ementa_bruta, sigla_tipo, numero, ano
    FROM projetos_brutos WHERE id = :id
""")

_UPDATE_PROJETO_STATUS_SQL = text("""
    UPDATE projetos_brutos SET status_processamento = :status WHERE id = :id
""")

_DEACTIVATE_PROMPT_SQL = text("""
    UPDATE historico_prompt SET ativo = FALSE WHERE ativo = TRUE
""")

_INSERT_PROMPT_SQL = text("""
    INSERT INTO historico_prompt (versao, descricao, system_prompt, ativo)
    VALUES (:versao, :descricao, :system_prompt, TRUE)
""")

_FETCH_AUDIT_HISTORY_SQL = text("""
    SELECT acao, usuario, dados_antes, dados_depois, observacao, registrado_em
    FROM auditoria
    WHERE entidade = 'projetos_brutos' AND id_entidade = :id
    ORDER BY registrado_em DESC
""")


class AuditLogger:
    """
    Registra ações de auditoria na tabela auditoria.
    Deve ser chamado SEMPRE que houver intervenção manual no banco.
    """

    def log(
        self,
        entidade:    str,
        id_entidade: int,
        acao:        str,
        usuario:     str = _SYSTEM_USER,
        dados_antes: dict | None = None,
        dados_depois: dict | None = None,
        observacao:  str | None = None,
    ) -> None:
        """
        Grava um registro de auditoria imutável.

        Args:
            entidade:     Nome da tabela afetada (ex: 'projetos_brutos').
            id_entidade:  PK do registro afetado.
            acao:         Descrição da ação (ex: 'APPROVE_QUARANTINE').
            usuario:      Quem executou a ação.
            dados_antes:  Snapshot do estado anterior (opcional).
            dados_depois: Snapshot do estado posterior (opcional).
            observacao:   Observação adicional (opcional).
        """
        with get_session() as session:
            try:
                session.execute(_INSERT_AUDIT_SQL, {
                    "entidade":     entidade,
                    "id_entidade":  id_entidade,
                    "acao":         acao,
                    "usuario":      usuario,
                    "dados_antes":  json.dumps(dados_antes)  if dados_antes  else None,
                    "dados_depois": json.dumps(dados_depois) if dados_depois else None,
                    "observacao":   observacao,
                })
                session.commit()
                logger.info(
                    "Auditoria registrada | entidade=%s | id=%d | acao=%s | usuario=%s",
                    entidade, id_entidade, acao, usuario,
                )
            except SQLAlchemyError as exc:
                session.rollback()
                logger.error("Falha ao gravar auditoria: %s", exc)
                raise


class QuarantineManager:
    """
    Permite aprovação ou rejeição de projetos em QUARENTENA com registro auditável.
    Toda ação gera entrada na tabela auditoria.
    """

    def __init__(self) -> None:
        self._audit = AuditLogger()

    def approve(self, projeto_id: int, usuario: str, observacao: str = "") -> bool:
        """
        Aprova um projeto em QUARENTENA, movendo-o para AGUARDANDO_PUBLICACAO.

        Args:
            projeto_id: ID do projeto em projetos_brutos.
            usuario:    Nome do revisor humano.
            observacao: Justificativa da aprovação.

        Returns:
            bool: True se aprovado com sucesso.
        """
        with get_session() as session:
            row = session.execute(_FETCH_PROJETO_SQL, {"id": projeto_id}).fetchone()

        if not row:
            logger.error("Projeto id=%d não encontrado.", projeto_id)
            return False

        if row.status_processamento != "QUARENTENA":
            logger.error(
                "Projeto id=%d não está em QUARENTENA (status atual: %s).",
                projeto_id, row.status_processamento,
            )
            return False

        dados_antes = {"status_processamento": row.status_processamento}

        with get_session() as session:
            try:
                session.execute(
                    _UPDATE_PROJETO_STATUS_SQL,
                    {"status": "AGUARDANDO_PUBLICACAO", "id": projeto_id},
                )
                session.commit()
            except SQLAlchemyError as exc:
                session.rollback()
                logger.error("Erro ao aprovar quarentena id=%d: %s", projeto_id, exc)
                return False

        self._audit.log(
            entidade="projetos_brutos",
            id_entidade=projeto_id,
            acao="APPROVE_QUARANTINE",
            usuario=usuario,
            dados_antes=dados_antes,
            dados_depois={"status_processamento": "AGUARDANDO_PUBLICACAO"},
            observacao=observacao or "Aprovação manual de quarentena.",
        )
        logger.info("Projeto id=%d aprovado da quarentena por '%s'.", projeto_id, usuario)
        return True

    def reject(self, projeto_id: int, usuario: str, observacao: str = "") -> bool:
        """
        Rejeita um projeto em QUARENTENA, marcando-o como IGNORADO.

        Args:
            projeto_id: ID do projeto.
            usuario:    Nome do revisor humano.
            observacao: Motivo da rejeição.

        Returns:
            bool: True se rejeitado com sucesso.
        """
        with get_session() as session:
            row = session.execute(_FETCH_PROJETO_SQL, {"id": projeto_id}).fetchone()

        if not row or row.status_processamento != "QUARENTENA":
            logger.error("Projeto id=%d não encontrado ou não está em QUARENTENA.", projeto_id)
            return False

        dados_antes = {"status_processamento": row.status_processamento}

        with get_session() as session:
            try:
                session.execute(
                    _UPDATE_PROJETO_STATUS_SQL,
                    {"status": "IGNORADO", "id": projeto_id},
                )
                session.commit()
            except SQLAlchemyError as exc:
                session.rollback()
                logger.error("Erro ao rejeitar quarentena id=%d: %s", projeto_id, exc)
                return False

        self._audit.log(
            entidade="projetos_brutos",
            id_entidade=projeto_id,
            acao="REJECT_QUARANTINE",
            usuario=usuario,
            dados_antes=dados_antes,
            dados_depois={"status_processamento": "IGNORADO"},
            observacao=observacao or "Rejeição manual de quarentena.",
        )
        logger.info("Projeto id=%d rejeitado da quarentena por '%s'.", projeto_id, usuario)
        return True

    def get_history(self, projeto_id: int) -> list[dict]:
        """Retorna o histórico de auditoria de um projeto."""
        with get_session() as session:
            rows = session.execute(
                _FETCH_AUDIT_HISTORY_SQL, {"id": projeto_id}
            ).fetchall()

        return [
            {
                "acao":         r.acao,
                "usuario":      r.usuario,
                "dados_antes":  json.loads(r.dados_antes)  if r.dados_antes  else None,
                "dados_depois": json.loads(r.dados_depois) if r.dados_depois else None,
                "observacao":   r.observacao,
                "registrado_em": r.registrado_em.isoformat(),
            }
            for r in rows
        ]


class PromptVersionManager:
    """
    Gerencia versões do System Prompt com controle de versão auditável.
    Ao ativar uma nova versão, a anterior é desativada automaticamente.
    """

    def __init__(self) -> None:
        self._audit = AuditLogger()

    def publish_new_version(
        self,
        versao:        str,
        system_prompt: str,
        descricao:     str,
        usuario:       str,
    ) -> bool:
        """
        Cria nova versão do System Prompt e desativa a anterior.

        Args:
            versao:        Identificador semântico (ex: "v1.1.0").
            system_prompt: Conteúdo completo do novo prompt.
            descricao:     Changelog da versão.
            usuario:       Responsável pela alteração.

        Returns:
            bool: True se publicado com sucesso.
        """
        with get_session() as session:
            try:
                session.execute(_DEACTIVATE_PROMPT_SQL)
                session.execute(_INSERT_PROMPT_SQL, {
                    "versao":        versao,
                    "descricao":     descricao,
                    "system_prompt": system_prompt,
                })
                session.commit()
            except SQLAlchemyError as exc:
                session.rollback()
                logger.error("Erro ao publicar nova versão de prompt: %s", exc)
                return False

        self._audit.log(
            entidade="historico_prompt",
            id_entidade=0,
            acao="PUBLISH_PROMPT_VERSION",
            usuario=usuario,
            dados_depois={"versao": versao, "descricao": descricao},
            observacao=f"Nova versão '{versao}' ativada.",
        )
        logger.info("Novo System Prompt publicado: versão %s por '%s'.", versao, usuario)
        return True
