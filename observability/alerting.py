"""
observability/alerting.py
Épico 5 — Sistema de Notificações e Alertas Críticos.

Canais suportados:
  - Slack (via Incoming Webhook)
  - Telegram (via Bot API)
  - E-mail (via SMTP)

Alertas implementados:
  - Crítico:    falha no pipeline, base offline, token expirado
  - Aviso:      rate limit, processamento parcial, quarentena
  - FinOps:     consumo de tokens atingiu 80% do orçamento mensal
  - Token Meta: aviso 7 dias antes da expiração do token da Graph API
"""

import os
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from dotenv import load_dotenv
from sqlalchemy import text

from models.database import get_session
from utils.logger import get_logger

load_dotenv()

logger = get_logger(__name__)

_SLACK_WEBHOOK    = os.getenv("SLACK_WEBHOOK_URL", "")
_TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
_TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
_SMTP_HOST        = os.getenv("SMTP_HOST", "smtp.gmail.com")
_SMTP_PORT        = int(os.getenv("SMTP_PORT", "587"))
_SMTP_USER        = os.getenv("SMTP_USER", "")
_SMTP_PASSWORD    = os.getenv("SMTP_PASSWORD", "")
_ALERT_FROM       = os.getenv("ALERT_FROM_EMAIL", _SMTP_USER)
_ALERT_TO_LIST    = [e.strip() for e in os.getenv("ALERT_TO_EMAILS", "").split(",") if e.strip()]

# Orçamento mensal de tokens (para alerta de 80%)
_MONTHLY_TOKEN_BUDGET = int(os.getenv("MONTHLY_TOKEN_BUDGET", "1000000"))


class Alerter:
    """Envia alertas para os canais configurados (Slack, Telegram, E-mail)."""

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def send_critical(self, message: str) -> None:
        """Alerta crítico: enviado imediatamente para todos os canais."""
        full_msg = f"🚨 [CRÍTICO] PautaLimpa\n{message}"
        logger.critical("ALERTA CRÍTICO: %s", message)
        self._dispatch(full_msg, level="critical")

    def send_warning(self, message: str) -> None:
        """Aviso: enviado para canais configurados."""
        full_msg = f"⚠️ [AVISO] PautaLimpa\n{message}"
        logger.warning("ALERTA AVISO: %s", message)
        self._dispatch(full_msg, level="warning")

    def send_info(self, message: str) -> None:
        """Informativo: enviado apenas ao Slack/Telegram (sem e-mail)."""
        full_msg = f"ℹ️ [INFO] PautaLimpa\n{message}"
        logger.info("ALERTA INFO: %s", message)
        self._slack(full_msg)
        self._telegram(full_msg)

    def check_finops(self) -> bool:
        """
        Verifica se o consumo de tokens do mês atual atingiu 80% do orçamento.
        Se sim, dispara alerta financeiro.

        Returns:
            bool: True se alerta foi disparado.
        """
        consumo = self._get_monthly_token_usage()
        threshold = int(_MONTHLY_TOKEN_BUDGET * 0.80)

        if consumo >= threshold:
            percentual = (consumo / _MONTHLY_TOKEN_BUDGET) * 100
            self.send_critical(
                f"FinOps: consumo de tokens atingiu {percentual:.1f}% do orçamento mensal.\n"
                f"Consumido: {consumo:,} / Orçamento: {_MONTHLY_TOKEN_BUDGET:,}\n"
                f"Considere pausar chamadas a APIs pagas ou ativar provedor gratuito."
            )
            return True

        logger.debug("FinOps OK: %d/%d tokens (%.1f%%)", consumo, _MONTHLY_TOKEN_BUDGET, (consumo/_MONTHLY_TOKEN_BUDGET)*100)
        return False

    def check_meta_token_expiry(self) -> bool:
        """
        Verifica se o token da Meta está próximo de expirar (≤ 7 dias).
        Requer META_TOKEN_EXPIRY_DATE no .env no formato YYYY-MM-DD.

        Returns:
            bool: True se alerta foi disparado.
        """
        expiry_str = os.getenv("META_TOKEN_EXPIRY_DATE", "")
        if not expiry_str:
            logger.debug("META_TOKEN_EXPIRY_DATE não configurado — verificação de expiração ignorada.")
            return False

        try:
            expiry = datetime.strptime(expiry_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            logger.error("META_TOKEN_EXPIRY_DATE inválido: %s. Use o formato YYYY-MM-DD.", expiry_str)
            return False

        dias_restantes = (expiry - datetime.now(timezone.utc)).days

        if dias_restantes <= 7:
            self.send_critical(
                f"Token da Meta expira em {dias_restantes} dia(s) ({expiry_str}).\n"
                "Renove o token IMEDIATAMENTE no Meta for Developers para evitar interrupção."
            )
            return True

        logger.info("Token Meta OK: expira em %d dia(s) (%s).", dias_restantes, expiry_str)
        return False

    def check_api_errors(self, consecutive_errors: int) -> bool:
        """
        Dispara alerta se o número de erros consecutivos da API pública ultrapassar 5.

        Args:
            consecutive_errors: Contador de erros consecutivos da sessão atual.

        Returns:
            bool: True se alerta foi disparado.
        """
        if consecutive_errors >= 5:
            self.send_critical(
                f"API da Câmara dos Deputados com {consecutive_errors} falhas consecutivas.\n"
                "Verifique a disponibilidade em: https://dadosabertos.camara.leg.br"
            )
            return True
        return False

    # ------------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------------

    def _dispatch(self, message: str, level: str) -> None:
        """Envia para todos os canais ativos."""
        self._slack(message)
        self._telegram(message)
        if level in ("critical", "warning"):
            self._email(message)

    def _slack(self, message: str) -> None:
        if not _SLACK_WEBHOOK:
            return
        try:
            requests.post(
                _SLACK_WEBHOOK,
                json={"text": message},
                timeout=10,
            )
        except requests.RequestException as exc:
            logger.error("Falha ao enviar alerta para Slack: %s", exc)

    def _telegram(self, message: str) -> None:
        if not _TELEGRAM_TOKEN or not _TELEGRAM_CHAT_ID:
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{_TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": _TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
                timeout=10,
            )
        except requests.RequestException as exc:
            logger.error("Falha ao enviar alerta para Telegram: %s", exc)

    def _email(self, message: str) -> None:
        if not _ALERT_TO_LIST or not _SMTP_USER:
            return
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "[PautaLimpa] Alerta do Sistema"
        msg["From"]    = _ALERT_FROM
        msg["To"]      = ", ".join(_ALERT_TO_LIST)
        msg.attach(MIMEText(f"<pre>{message}</pre>", "html", "utf-8"))
        try:
            with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as server:
                server.ehlo()
                server.starttls()
                server.login(_SMTP_USER, _SMTP_PASSWORD)
                server.sendmail(_ALERT_FROM, _ALERT_TO_LIST, msg.as_string())
        except smtplib.SMTPException as exc:
            logger.error("Falha ao enviar e-mail de alerta: %s", exc)

    def _get_monthly_token_usage(self) -> int:
        """Soma tokens usados no mês corrente via banco de dados."""
        sql = text("""
            SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0)
            FROM processamento_ia
            WHERE data_processamento >= DATE_TRUNC('month', NOW())
              AND status_ia IN ('SUCESSO', 'FALLBACK_UTILIZADO')
        """)
        with get_session() as session:
            result = session.execute(sql)
            return result.scalar() or 0
