"""Telegram command-center webhook routes."""

from __future__ import annotations

import json
from time import perf_counter
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, Header
import httpx
from sqlalchemy.orm import Session

from src.control.command_router import CommandContext, dispatch_command
from src.control.command_schema import build_idempotency_key, parse_command, parse_envelope
from src.control.security import ControlAuthorizationError, resolve_control_actor
from src.control.services import create_admin_action
from src.core.config import get_settings
from src.core.logger import get_logger
from src.integrations.x.x_client import XClient, get_x_client
from src.schemas.control import ControlWebhookResponse
from src.storage.db import get_session
from src.storage.redis_client import get_client as get_redis_client
from src.storage.tenant import set_workspace_context


router = APIRouter(prefix="/control/telegram", tags=["control-telegram"])
logger = get_logger("revfirst.control.telegram")


def _verify_webhook_secret(received_secret: str | None) -> None:
    settings = get_settings()
    configured = settings.telegram_webhook_secret.strip()
    if not configured:
        return
    if received_secret != configured:
        raise ControlAuthorizationError("invalid_telegram_webhook_secret")


def _truncate_text(value: str, *, limit: int) -> str:
    normalized = " ".join((value or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _render_queue_reply(data: Dict[str, Any]) -> str:
    items = data.get("items")
    if not isinstance(items, list) or not items:
        return (
            "Fila de aprovacao vazia.\n"
            "Sugestao: rode /run daily_post ou /run propose_replies."
        )

    lines = [f"Fila de aprovacao ({len(items)} item(ns)):"]
    for index, row in enumerate(items[:5], start=1):
        if not isinstance(row, dict):
            continue
        queue_id = str(row.get("queue_id") or row.get("id") or "sem-id")
        item_type = str(row.get("type") or "item").upper()
        copy_text = _truncate_text(str(row.get("copy") or row.get("preview") or ""), limit=220)
        image_url = str(row.get("image_url") or "").strip()
        lines.append(f"{index}. {item_type} | {queue_id}")
        lines.append(f"Copy: {copy_text or '(vazio)'}")
        lines.append(f"Imagem: {image_url if image_url else 'sem imagem'}")
        lines.append(f"Acoes: /preview {queue_id} | /approve {queue_id}")
    return "\n".join(lines)


def _render_status_reply(data: Dict[str, Any]) -> str:
    paused = bool(data.get("paused"))
    channels = data.get("channels") if isinstance(data.get("channels"), dict) else {}
    enabled_channels = sorted([name for name, is_enabled in channels.items() if bool(is_enabled)])
    last_runs = data.get("last_runs") if isinstance(data.get("last_runs"), dict) else {}
    last_run_summary = "nenhum"
    if last_runs:
        first_pipeline = sorted(last_runs.keys())[0]
        pipeline_data = last_runs.get(first_pipeline)
        if isinstance(pipeline_data, dict):
            last_run_summary = f"{first_pipeline}={pipeline_data.get('status')}"
    return (
        "Status do workspace:\n"
        f"- Pausado: {'sim' if paused else 'nao'}\n"
        f"- Canais ativos: {', '.join(enabled_channels) if enabled_channels else 'nenhum'}\n"
        f"- Ultimo run: {last_run_summary}\n"
        "Proxima acao: use /queue para revisar pendencias."
    )


def _render_pipeline_reply(data: Dict[str, Any]) -> str:
    pipeline = str(data.get("pipeline") or "pipeline")
    result = data.get("result") if isinstance(data.get("result"), dict) else {}
    queued = result.get("queued")
    queued_types = result.get("queued_types") if isinstance(result.get("queued_types"), list) else []
    queued_types_text = ", ".join(str(value) for value in queued_types if str(value).strip()) or "nenhum"
    return (
        f"Pipeline executado: {pipeline}\n"
        f"Itens enfileirados: {queued if queued is not None else 0}\n"
        f"Tipos: {queued_types_text}\n"
        "Proxima acao: rode /queue e aprove os itens."
    )


def _render_preview_reply(data: Dict[str, Any], *, with_image: bool) -> str:
    queue_id = str(data.get("queue_id") or "sem-id")
    item_type = str(data.get("item_type") or "item").upper()
    copy_text = _truncate_text(str(data.get("copy") or ""), limit=260)
    image_url = str(data.get("image_url") or "").strip()
    lines = [
        f"Preview pronto para {item_type}.",
        f"queue_id: {queue_id}",
        f"Copy: {copy_text or '(vazio)'}",
    ]
    if with_image:
        lines.append("Imagem enviada no chat.")
    else:
        lines.append(f"Imagem: {image_url if image_url else 'indisponivel'}")
    lines.append(f"Proxima acao: /approve {queue_id}")
    return "\n".join(lines)


def _render_growth_reply(message: str, data: Dict[str, Any]) -> str:
    kpis = data.get("kpis") if isinstance(data.get("kpis"), dict) else {}
    engagement = kpis.get("engagement") if isinstance(kpis.get("engagement"), dict) else {}
    period_days = int(data.get("period_days") or kpis.get("period_days") or (7 if message == "growth_weekly_ok" else 1))
    recommendations = data.get("recommendations") if isinstance(data.get("recommendations"), list) else []

    lines = [
        f"Relatorio de crescimento ({period_days} dia(s)):",
        f"- Posts publicados: {int(kpis.get('published_posts') or 0)}",
        f"- Replies publicadas: {int(kpis.get('published_replies') or 0)}",
        f"- Falhas de publicacao: {int(kpis.get('failed_publications') or 0)}",
        f"- Delta de seguidores: {kpis.get('follower_delta') if kpis.get('follower_delta') is not None else 'n/d'}",
        f"- Engajamento medio (likes/replies): {engagement.get('avg_likes', 0.0)}/{engagement.get('avg_replies', 0.0)}",
    ]
    if recommendations:
        lines.append(f"Acao sugerida: {str(recommendations[0])}")
    return "\n".join(lines)


def _render_strategy_scan_reply(data: Dict[str, Any]) -> str:
    status = str(data.get("status") or "unknown")
    watchlist_count = int(data.get("watchlist_count") or 0)
    ingested_posts = int(data.get("ingested_posts") or 0)
    if status in {"missing_x_oauth", "no_watchlist", "no_data"}:
        return (
            "Scan de estrategia nao concluido.\n"
            f"status: {status}\n"
            f"watchlist: {watchlist_count}\n"
            "Proxima acao: adicione conta alvo com /strategy_scan <account_user_id> <username>."
        )

    recommendations = data.get("recommendations") if isinstance(data.get("recommendations"), list) else []
    top_recommendation = str(recommendations[0]) if recommendations else "Revisar pattern extraido."
    confidence = int(data.get("confidence_score") or 0)
    return (
        "Scan de estrategia concluido.\n"
        f"- Contas na watchlist: {watchlist_count}\n"
        f"- Posts ingeridos: {ingested_posts}\n"
        f"- Confianca do padrao: {confidence}\n"
        f"Acao sugerida: {top_recommendation}"
    )


def _render_strategy_report_reply(data: Dict[str, Any]) -> str:
    recommendations = data.get("recommendations") if isinstance(data.get("recommendations"), list) else []
    first_recommendation = str(recommendations[0]) if recommendations else "Sem recomendacoes no momento."
    return (
        "Relatorio de estrategia benchmark:\n"
        f"- Watchlist ativa: {int(data.get('watchlist_count') or 0)}\n"
        f"- Janela: {str(data.get('period_window') or 'n/d')}\n"
        f"- Confianca: {int(data.get('confidence_score') or 0)}\n"
        f"Acao sugerida: {first_recommendation}"
    )


def _render_chat_reply(response: ControlWebhookResponse) -> str:
    data = dict(response.data or {})
    data.pop("preview_photo", None)

    if response.message == "available_commands":
        commands = data.get("commands") if isinstance(data.get("commands"), list) else []
        if commands:
            body = "Comandos disponiveis:\n" + "\n".join(str(command) for command in commands)
        else:
            body = "Comandos disponiveis: /help"
    elif response.message == "queue_ok":
        body = _render_queue_reply(data)
    elif response.message == "status_ok":
        body = _render_status_reply(data)
    elif response.message == "pipeline_executed":
        body = _render_pipeline_reply(data)
    elif response.message == "preview_ready":
        body = _render_preview_reply(data, with_image=True)
    elif response.message == "preview_image_unavailable":
        body = _render_preview_reply(data, with_image=False)
    elif response.message in {"growth_report_ok", "growth_weekly_ok"}:
        body = _render_growth_reply(response.message, data)
    elif response.message == "strategy_scan_ok":
        body = _render_strategy_scan_reply(data)
    elif response.message == "strategy_report_ok":
        body = _render_strategy_report_reply(data)
    elif response.message == "strategy_watchlist_updated":
        body = (
            "Conta adicionada na watchlist de estrategia.\n"
            f"account_user_id: {data.get('account_user_id')}\n"
            f"username: {data.get('account_username') or 'n/d'}\n"
            "Proxima acao: /strategy_scan run"
        )
    elif response.message == "strategy_report_empty":
        body = "Relatorio de estrategia ainda vazio. Rode /strategy_scan run primeiro."
    elif response.message == "strategy_scan_not_ready":
        body = _render_strategy_scan_reply(data)
    elif response.message == "approved_and_published":
        queue_id = data.get("queue_id")
        external_post_id = data.get("external_post_id")
        body = (
            "Publicado com sucesso.\n"
            f"queue_id: {queue_id}\n"
            f"post_id: {external_post_id}\n"
            "Proxima acao: rode /queue para o proximo item."
        )
    elif response.message == "approve_publish_failed":
        queue_id = data.get("queue_id")
        error = data.get("error") or response.message
        body = (
            "Falha ao publicar item aprovado.\n"
            f"queue_id: {queue_id}\n"
            f"erro: {error}\n"
            "Proxima acao: revise /preview <queue_id> e tente novamente."
        )
    elif response.message == "no_pending_queue_item":
        body = "Nao ha item pendente para aprovar. Use /queue para verificar."
    elif response.message == "missing_queue_id":
        body = "Informe o queue_id. Exemplo: /approve <queue_id> ou /preview <queue_id>."
    elif response.message == "unknown_command":
        body = "Comando nao reconhecido. Use /help para ver comandos disponiveis."
    elif response.message == "unauthorized":
        body = "Usuario sem permissao para esse comando."
    else:
        if data:
            payload_text = json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            body = f"{response.message}\n{payload_text}"
        else:
            body = response.message

    if len(body) <= 4096:
        return body
    return f"{body[:4078]}...(truncated)"


def _send_telegram_chat_photo(*, chat_id: str, image_url: str, caption: str | None = None) -> None:
    settings = get_settings()
    token = settings.telegram_bot_token.strip()
    normalized_chat_id = (chat_id or "").strip()
    normalized_image_url = (image_url or "").strip()
    if not token or not normalized_chat_id or not normalized_image_url:
        return

    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    payload: Dict[str, Any] = {
        "chat_id": normalized_chat_id,
        "photo": normalized_image_url,
    }
    if caption:
        payload["caption"] = _truncate_text(caption, limit=1024)

    try:
        with httpx.Client(timeout=12) as client:
            response = client.post(url, json=payload)
    except httpx.HTTPError as exc:
        logger.warning("telegram_send_photo_transport_failed", chat_id=normalized_chat_id, error=str(exc))
        return

    if response.status_code >= 400:
        logger.warning(
            "telegram_send_photo_failed",
            chat_id=normalized_chat_id,
            status_code=response.status_code,
            response_text=response.text[:255],
        )


def _send_telegram_chat_message(*, chat_id: str, text: str) -> None:
    settings = get_settings()
    token = settings.telegram_bot_token.strip()
    normalized_chat_id = (chat_id or "").strip()
    normalized_text = (text or "").strip()
    if not token or not normalized_chat_id or not normalized_text:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": normalized_chat_id,
        "text": normalized_text,
        "disable_web_page_preview": True,
    }
    try:
        with httpx.Client(timeout=10) as client:
            response = client.post(url, json=payload)
    except httpx.HTTPError as exc:
        logger.warning("telegram_send_message_transport_failed", chat_id=normalized_chat_id, error=str(exc))
        return

    if response.status_code >= 400:
        logger.warning(
            "telegram_send_message_failed",
            chat_id=normalized_chat_id,
            status_code=response.status_code,
            response_text=response.text[:255],
        )


def _return_with_chat_reply(*, envelope_chat_id: Optional[str], response: ControlWebhookResponse) -> ControlWebhookResponse:
    if envelope_chat_id:
        preview = response.data.get("preview_photo") if isinstance(response.data, dict) else None
        if isinstance(preview, dict):
            image_url = str(preview.get("image_url") or "").strip()
            caption = str(preview.get("caption") or "").strip() or None
            _send_telegram_chat_photo(chat_id=envelope_chat_id, image_url=image_url, caption=caption)
        _send_telegram_chat_message(chat_id=envelope_chat_id, text=_render_chat_reply(response))
    return response


@router.post("/webhook/{workspace_id}", response_model=ControlWebhookResponse)
def control_webhook(
    workspace_id: str,
    payload: Dict[str, Any],
    session: Session = Depends(get_session),
    x_client: XClient = Depends(get_x_client),
    telegram_secret_token: Optional[str] = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
) -> ControlWebhookResponse:
    request_id = f"ctl-{uuid4()}"
    started_at = perf_counter()

    set_workspace_context(session, workspace_id)

    envelope = parse_envelope(workspace_id=workspace_id, payload=payload)
    if envelope is None:
        return ControlWebhookResponse(
            accepted=False,
            workspace_id=workspace_id,
            request_id=request_id,
            command=None,
            status="ignored",
            message="message_not_supported",
        )

    command = parse_command(envelope.text)
    if command is None:
        return _return_with_chat_reply(
            envelope_chat_id=envelope.chat_id,
            response=ControlWebhookResponse(
                accepted=False,
                workspace_id=workspace_id,
                request_id=request_id,
                command=None,
                status="ignored",
                message="message_is_not_command",
            ),
        )

    idempotency_key = build_idempotency_key(update_id=envelope.update_id, command_text=command.raw_text)

    try:
        _verify_webhook_secret(telegram_secret_token)
        actor = resolve_control_actor(
            session,
            workspace_id=workspace_id,
            telegram_user_id=envelope.telegram_user_id,
        )

        context = CommandContext(
            session=session,
            redis_client=get_redis_client(),
            x_client=x_client,
            envelope=envelope,
            command=command,
            actor=actor,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
        response = dispatch_command(context)

        duration_ms = int((perf_counter() - started_at) * 1000)
        create_admin_action(
            session,
            workspace_id=workspace_id,
            actor_user_id=actor.user_id,
            telegram_user_id=envelope.telegram_user_id,
            command=command.name,
            payload={"args": command.args, "text": command.raw_text},
            status="success" if response.success else "error",
            result_summary=response.message,
            error_message=None if response.success else response.message,
            duration_ms=duration_ms,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )

        return _return_with_chat_reply(
            envelope_chat_id=envelope.chat_id,
            response=ControlWebhookResponse(
                accepted=response.success,
                workspace_id=workspace_id,
                request_id=request_id,
                command=command.name,
                status="ok" if response.success else "error",
                message=response.message,
                data=response.data,
            ),
        )
    except ControlAuthorizationError as exc:
        duration_ms = int((perf_counter() - started_at) * 1000)
        create_admin_action(
            session,
            workspace_id=workspace_id,
            actor_user_id=None,
            telegram_user_id=envelope.telegram_user_id,
            command=command.name,
            payload={"args": command.args, "text": command.raw_text},
            status="unauthorized",
            result_summary="unauthorized",
            error_message=str(exc),
            duration_ms=duration_ms,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
        return _return_with_chat_reply(
            envelope_chat_id=envelope.chat_id,
            response=ControlWebhookResponse(
                accepted=False,
                workspace_id=workspace_id,
                request_id=request_id,
                command=command.name,
                status="unauthorized",
                message="unauthorized",
                data={"reason": str(exc)},
            ),
        )
    except Exception as exc:
        duration_ms = int((perf_counter() - started_at) * 1000)
        create_admin_action(
            session,
            workspace_id=workspace_id,
            actor_user_id=None,
            telegram_user_id=envelope.telegram_user_id,
            command=command.name,
            payload={"args": command.args, "text": command.raw_text},
            status="error",
            result_summary="execution_error",
            error_message=str(exc),
            duration_ms=duration_ms,
            request_id=request_id,
            idempotency_key=idempotency_key,
        )
        return _return_with_chat_reply(
            envelope_chat_id=envelope.chat_id,
            response=ControlWebhookResponse(
                accepted=False,
                workspace_id=workspace_id,
                request_id=request_id,
                command=command.name,
                status="error",
                message="execution_error",
                data={"error": str(exc)},
            ),
        )
