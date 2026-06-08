"""
pipeline/quarantine.py
Epico 3 - Fila de Quarentena e Validacao de Saida da IA.

Responsabilidades:
  - Validar se o texto gerado pela LLM segue as 5 regras
  - Detectar frases de recusa do modelo
  - Mover registros invalidos para QUARENTENA e notificar a equipe
  - Enviar relatorio diario por e-mail com IDs em quarentena
"""

import os
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv
from collections import defaultdict

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from models.database import get_session
from utils.logger import get_logger

load_dotenv()

logger = get_logger(__name__)

_MAX_RESPONSE_CHARS = 2200
_MIN_BODY_CHARS = 300

# Regex que valida a Regra 4: primeira linha = "[TIPO] - [NUMERO]/[ANO]"
_RE_CITACAO_OBRIGATORIA = re.compile(
    r"^\s*[A-Z]{2,10}\s*-\s*\d{1,6}/\d{4}\s*$",
    re.MULTILINE,
)

# Frases que indicam recusa ou resposta invalida da IA
_REFUSAL_PHRASES = [
    "desculpe, nao posso",
    "desculpe, não posso",
    "sorry, i cannot",
    "nao e possivel analisar",
    "não é possível analisar",
    "nao consigo processar",
    "não consigo processar",
    "como modelo de linguagem",
    "nao tenho capacidade",
    "não tenho capacidade",
    "como assistente de ia",
    "nao posso fornecer",
    "não posso fornecer",
]

_ANALYSIS_ORDER = ("IMPARCIAL", "DIREITA", "ESQUERDA")

_FETCH_QUARANTINE_CANDIDATES_SQL = text("""
    SELECT
        pb.id          AS projeto_id,
        pb.sigla_tipo,
        pb.numero,
        pb.ano,
        COALESCE(ai.tipo_analise::text, 'IMPARCIAL') AS tipo_analise,
        COALESCE(ai.texto_traduzido, pia.texto_traduzido) AS texto_traduzido,
        COALESCE(ai.status_ia::text, pia.status_ia::text, 'PENDENTE') AS status_ia
    FROM projetos_brutos pb
    LEFT JOIN analises_ia ai
        ON ai.fk_projeto = pb.id
    LEFT JOIN processamento_ia pia
        ON pia.fk_projeto = pb.id
       AND ai.id IS NULL
    WHERE pb.status_processamento = 'AGUARDANDO_MIDIA'
      AND (
        ai.id IS NOT NULL
        OR pia.id IS NOT NULL
      )
    ORDER BY pb.id DESC, tipo_analise
""")

_UPDATE_STATUS_SQL = text("""
    UPDATE projetos_brutos
    SET status_processamento = :status
    WHERE id = :id
""")

_FETCH_DAILY_QUARANTINE_SQL = text("""
    SELECT pb.id, pb.sigla_tipo, pb.numero, pb.ano, pb.data_atualizacao
    FROM projetos_brutos pb
    WHERE pb.status_processamento = 'QUARENTENA'
      AND pb.data_atualizacao >= NOW() - INTERVAL '24 hours'
    ORDER BY pb.data_atualizacao DESC
""")


def _has_valid_citation(texto: str) -> bool:
    """Verifica se a primeira linha contem a citacao obrigatoria."""
    primeira_linha = texto.strip().split("\n")[0]
    return bool(_RE_CITACAO_OBRIGATORIA.search(primeira_linha))


def _has_valid_length(texto: str) -> tuple[bool, str | None]:
    """Valida o tamanho total da resposta e do corpo explicativo."""
    texto_normalizado = (texto or "").strip()

    if len(texto_normalizado) > _MAX_RESPONSE_CHARS:
        return False, f"Resposta acima de {_MAX_RESPONSE_CHARS} caracteres"

    linhas = texto_normalizado.splitlines()
    corpo = "\n".join(linhas[1:]).strip() if len(linhas) > 1 else ""
    if len(corpo) < _MIN_BODY_CHARS:
        return False, f"Texto explicativo com menos de {_MIN_BODY_CHARS} caracteres"

    return True, None


def _is_refusal(texto: str) -> bool:
    """Detecta frases de recusa ou resposta invalida da IA."""
    texto_lower = texto.lower()
    return any(phrase in texto_lower for phrase in _REFUSAL_PHRASES)


def _get_analysis_status(projeto_id: int, tipo_analise: str) -> str:
    with get_session() as session:
        row = session.execute(
            text("""
                SELECT status_ia
                FROM analises_ia
                WHERE fk_projeto = :projeto_id
                  AND tipo_analise = :tipo_analise
            """),
            {"projeto_id": projeto_id, "tipo_analise": tipo_analise},
        ).fetchone()
    return row.status_ia if row else "PENDENTE"


class QuarantineValidator:
    """
    Percorre projetos em AGUARDANDO_MIDIA e valida a saida da LLM.
    Registros que falham nas validacoes sao movidos para QUARENTENA.
    """

    def validate_all(self) -> dict[str, int]:
        """
        Valida todos os projetos em AGUARDANDO_MIDIA.

        Returns:
            dict: {"validados": N, "aprovados": N, "quarentena": N}
        """
        stats = {"validados": 0, "aprovados": 0, "quarentena": 0}

        with get_session() as session:
            rows = session.execute(_FETCH_QUARANTINE_CANDIDATES_SQL).fetchall()

        grouped = defaultdict(dict)
        meta = {}
        for row in rows:
            meta[row.projeto_id] = row
            grouped[row.projeto_id][row.tipo_analise] = row.texto_traduzido or ""

        for projeto_id, analyses in grouped.items():
            row = meta[projeto_id]
            stats["validados"] += 1
            motivos = []

            has_new_flow = any(
                _get_analysis_status(row.projeto_id, tipo_analise) != "PENDENTE"
                for tipo_analise in _ANALYSIS_ORDER
            )

            if has_new_flow:
                for tipo_analise in _ANALYSIS_ORDER:
                    texto = analyses.get(tipo_analise, "")
                    status_ia = _get_analysis_status(row.projeto_id, tipo_analise)

                    if status_ia == "PENDENTE":
                        motivos.append(f"Analise ausente: {tipo_analise}")
                        continue
                    if status_ia not in ("SUCESSO", "FALLBACK_UTILIZADO"):
                        motivos.append(f"{tipo_analise}: status IA inválido ({status_ia})")
                        continue
                    if not texto:
                        motivos.append(f"Analise vazia: {tipo_analise}")
                        continue
                    if _is_refusal(texto):
                        motivos.append(f"Recusa do modelo em {tipo_analise}")
                        continue
                    if not _has_valid_citation(texto):
                        motivos.append(f"Citacao obrigatoria ausente em {tipo_analise}")
                        continue
                    comprimento_valido, motivo_comprimento = _has_valid_length(texto)
                    if not comprimento_valido:
                        motivos.append(f"{tipo_analise}: {motivo_comprimento}")
            else:
                texto = next(iter(analyses.values()), "")
                if _is_refusal(texto):
                    motivos.append("Recusa do modelo detectada no texto gerado")
                elif not _has_valid_citation(texto):
                    motivos.append("Citacao obrigatoria ausente (Regra 4 violada)")
                else:
                    comprimento_valido, motivo_comprimento = _has_valid_length(texto)
                    if not comprimento_valido:
                        motivos.append(motivo_comprimento)

            if motivos:
                self._quarantine(row.projeto_id, "; ".join(motivos))
                stats["quarentena"] += 1
                logger.warning(
                    "QUARENTENA | id=%d | %s %s/%s | motivo: %s",
                    row.projeto_id, row.sigla_tipo, row.numero, row.ano, "; ".join(motivos),
                )
            else:
                stats["aprovados"] += 1
                logger.debug(
                    "APROVADO | id=%d | %s %s/%s - citacao e conteudo validos.",
                    row.projeto_id, row.sigla_tipo, row.numero, row.ano,
                )

        logger.info(
            "Validacao concluida | validados=%d | aprovados=%d | quarentena=%d",
            stats["validados"], stats["aprovados"], stats["quarentena"],
        )
        return stats

    def _quarantine(self, projeto_id: int, motivo: str) -> None:
        with get_session() as session:
            try:
                session.execute(
                    _UPDATE_STATUS_SQL,
                    {"status": "QUARENTENA", "id": projeto_id},
                )
                session.commit()
            except SQLAlchemyError as exc:
                session.rollback()
                logger.error("Erro ao mover id=%d para QUARENTENA: %s", projeto_id, exc)
                raise


class QuarantineReporter:
    """Gera e envia relatorio diario por e-mail com IDs em quarentena."""

    def __init__(self) -> None:
        self._smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        self._smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self._smtp_user = os.getenv("SMTP_USER", "")
        self._smtp_password = os.getenv("SMTP_PASSWORD", "")
        self._from_email = os.getenv("ALERT_FROM_EMAIL", self._smtp_user)
        self._to_emails = [
            e.strip()
            for e in os.getenv("ALERT_TO_EMAILS", "").split(",")
            if e.strip()
        ]

    def send_daily_report(self) -> bool:
        """
        Busca IDs em quarentena das ultimas 24h e envia e-mail para a equipe.

        Returns:
            bool: True se e-mail enviado com sucesso ou sem itens para reportar.
        """
        with get_session() as session:
            rows = session.execute(_FETCH_DAILY_QUARANTINE_SQL).fetchall()

        if not rows:
            logger.info("Relatorio de quarentena: nenhum item nas ultimas 24h.")
            return True

        if not self._to_emails:
            logger.warning("ALERT_TO_EMAILS nao configurado - relatorio de quarentena nao enviado.")
            return False

        items_html = "".join(
            f"<tr><td>{r.id}</td><td>{r.sigla_tipo} {r.numero}/{r.ano}</td>"
            f"<td>{r.data_atualizacao.strftime('%d/%m/%Y %H:%M')}</td></tr>"
            for r in rows
        )
        body = f"""
        <html><body>
        <h2>PautaLimpa - Relatorio Diario de Quarentena</h2>
        <p>{len(rows)} item(s) movido(s) para quarentena nas ultimas 24 horas:</p>
        <table border="1" cellpadding="5">
          <tr><th>ID</th><th>Projeto</th><th>Data</th></tr>
          {items_html}
        </table>
        <p>Acesse o banco para revisar e aprovar ou descartar cada item.</p>
        </body></html>
        """
        return self._send_email(
            subject=f"[PautaLimpa] {len(rows)} item(s) em Quarentena - Revisao Necessaria",
            html_body=body,
        )

    def _send_email(self, subject: str, html_body: str) -> bool:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self._from_email
        msg["To"] = ", ".join(self._to_emails)
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        try:
            with smtplib.SMTP(self._smtp_host, self._smtp_port) as server:
                server.ehlo()
                server.starttls()
                server.login(self._smtp_user, self._smtp_password)
                server.sendmail(self._from_email, self._to_emails, msg.as_string())
            logger.info("E-mail de quarentena enviado para: %s", self._to_emails)
            return True
        except smtplib.SMTPException as exc:
            logger.error("Falha ao enviar e-mail de quarentena: %s", exc)
            return False
