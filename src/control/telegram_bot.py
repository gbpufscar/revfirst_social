"""Telegram command-center webhook routes."""

from __future__ import annotations

import json
from datetime import datetime, timezone
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


def _safe_plain_text(value: str) -> str:
    normalized = " ".join((value or "").split())
    # We render plain text, but keep IDs in backticks; avoid accidental formatting in user content.
    return normalized.replace("`", "'")


def _short_queue_id(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return "sem-id"
    return normalized[:8]


def _queue_status_label(status: str) -> str:
    mapping = {
        "pending": "Pending Review",
        "pending_review": "Pending Review",
        "approved": "Approved Scheduled",
        "approved_scheduled": "Approved Scheduled",
        "publishing": "Publishing",
        "published": "Published",
        "rejected": "Rejected",
        "failed": "Failed",
    }
    normalized = str(status or "").strip().lower()
    return mapping.get(normalized, normalized or "Pending Review")


def _format_hhmm_utc(raw_value: Any) -> str:
    normalized = str(raw_value or "").strip()
    if not normalized:
        return "n/d"
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return "n/d"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    return parsed.strftime("%H:%M UTC")


def _status_risk_level(*, mode: str, global_kill_switch: bool, telegram_status: str, recent_errors: int) -> str:
    normalized_mode = str(mode or "").strip().lower()
    normalized_telegram = str(telegram_status or "").strip().upper()
    if global_kill_switch:
        return "CRITICAL"
    if normalized_mode == "containment":
        return "HIGH"
    if normalized_telegram == "DEGRADED":
        return "HIGH"
    if recent_errors > 0:
        return "MEDIUM"
    return "LOW"


def _render_approval_confirmation(data: Dict[str, Any]) -> str:
    queue_id = str(data.get("queue_id") or "").strip()
    short_id = _short_queue_id(queue_id)
    scheduled_for = _format_hhmm_utc(data.get("scheduled_for"))
    return (
        "✅ APPROVED\n"
        f"ID: `{short_id}`\n"
        "\n"
        "Scheduled For:\n"
        f"{scheduled_for}\n"
        "\n"
        "Status:\n"
        "Approved Scheduled\n"
        "\n"
        "Next Window:\n"
        f"{scheduled_for}"
    )


def _render_publish_confirmation(data: Dict[str, Any]) -> str:
    queue_id = str(data.get("queue_id") or "").strip()
    short_id = _short_queue_id(queue_id)
    external_post_id = str(data.get("external_post_id") or "n/d")
    return (
        "✅ PUBLISHED\n"
        f"ID: `{short_id}`\n"
        "\n"
        "Status:\n"
        "Published\n"
        "\n"
        "Post ID:\n"
        f"{external_post_id}"
    )


def _render_alert(
    *,
    alert_type: str,
    workspace: str,
    action: str,
    required: str,
) -> str:
    return (
        "🚨 ALERT\n"
        "Type:\n"
        f"{alert_type}\n"
        "\n"
        "Workspace:\n"
        f"{workspace}\n"
        "\n"
        "Action:\n"
        f"{action}\n"
        "\n"
        "Required:\n"
        f"{required}"
    )


def _render_reject_confirmation(data: Dict[str, Any], *, regenerated: bool) -> str:
    queue_id = str(data.get("queue_id") or "").strip()
    short_id = _short_queue_id(queue_id)
    replacement = "Not generated"
    if regenerated:
        auto_regen = data.get("auto_regeneration") if isinstance(data.get("auto_regeneration"), dict) else {}
        next_queue_id = str(auto_regen.get("queue_id") or "").strip()
        replacement = f"Generated ({_short_queue_id(next_queue_id)})" if next_queue_id else "Generated"
    return (
        "❌ REJECTED\n"
        f"ID: `{short_id}`\n"
        "\n"
        "Status:\n"
        "Rejected\n"
        "\n"
        "Replacement Draft:\n"
        f"{replacement}"
    )


def _render_stability_alert(data: Dict[str, Any], *, containment_mode: bool = False) -> str:
    overall_status = str(data.get("overall_status") or "unknown").strip().lower()
    workspace = str(data.get("workspace_id") or "revfirst")
    critical_count = int(data.get("critical_count") or 0)
    warning_count = int(data.get("warning_count") or 0)
    recommended_actions = data.get("recommended_actions") if isinstance(data.get("recommended_actions"), list) else []
    actions_applied = data.get("actions_applied") if isinstance(data.get("actions_applied"), list) else []
    kill_switch_action = data.get("kill_switch_action") if isinstance(data.get("kill_switch_action"), dict) else {}

    severity_label = {
        "critical": "CRITICAL",
        "warning": "HIGH",
        "healthy": "LOW",
    }.get(overall_status, "MEDIUM")
    alert_type = f"Stability ({severity_label})"

    action = (
        f"Critical checks: {critical_count}; warning checks: {warning_count}."
        if overall_status in {"critical", "warning"}
        else "Stability checks healthy."
    )
    if containment_mode and actions_applied:
        action = f"Containment applied: {', '.join(str(value) for value in actions_applied)}."
    if kill_switch_action.get("applied"):
        ttl_seconds = kill_switch_action.get("ttl_seconds")
        action = f"Global kill-switch activated (ttl={ttl_seconds}s)."

    if overall_status == "critical":
        required = "/stability contain"
    elif overall_status == "warning":
        required = "/stability"
    else:
        required = "none"
    if kill_switch_action.get("applied"):
        required = "/ack_kill_switch"
    if recommended_actions:
        required = f"{required} | {str(recommended_actions[0])}" if required != "none" else str(recommended_actions[0])

    return _render_alert(
        alert_type=alert_type,
        workspace=workspace,
        action=action,
        required=required,
    )


def _render_publish_failure_alert(data: Dict[str, Any]) -> str:
    workspace = str(data.get("workspace_id") or "revfirst")
    error = str(data.get("error") or "publish_failed")
    publish_status = str(data.get("publish_status") or "").strip().lower()
    normalized = error.lower()

    alert_type = "Publish Failure"
    action = error
    required = "/preview <queue_id> e tentar novamente."

    if publish_status == "blocked_plan" or "plan limit exceeded" in normalized or "blocked_plan" in normalized:
        alert_type = "Plan Limit"
        action = "Publishing blocked by plan quota."
        required = "/metrics para revisar uso e ajustar plano."
    elif publish_status == "blocked_rate_limit" or "blocked_rate_limit" in normalized or "rate limit" in normalized:
        alert_type = "Rate Limit"
        action = "Publishing blocked by hourly quota/rate limit."
        required = "Aguardar janela e rodar /queue novamente."
    elif (
        publish_status == "blocked_circuit_breaker"
        or "blocked_circuit_breaker" in normalized
        or "circuit breaker" in normalized
    ):
        alert_type = "Circuit Breaker"
        action = "Publishing blocked by consecutive failure breaker."
        required = "/stability e diagnostico antes de retomar."
    elif publish_status == "blocked_mode" or ("operational mode" in normalized and "containment" in normalized):
        alert_type = "Containment"
        action = "Publishing blocked by containment mode."
        required = "Owner/admin com override apos diagnostico."

    return _render_alert(
        alert_type=alert_type,
        workspace=workspace,
        action=action,
        required=required,
    )


def _render_queue_reply(data: Dict[str, Any]) -> str:
    items = data.get("items")
    if not isinstance(items, list) or not items:
        return "📋 Approval Queue is empty."

    sections = [f"📋 Approval Queue ({len(items)} items)"]
    for row in items[:5]:
        if not isinstance(row, dict):
            continue
        queue_id = str(row.get("queue_id") or row.get("id") or "").strip()
        short_id = _short_queue_id(queue_id)
        item_type = str(row.get("type") or "item").upper()
        copy_text = _truncate_text(
            _safe_plain_text(str(row.get("copy") or row.get("preview") or "")),
            limit=300,
        )
        image_url = str(row.get("image_url") or "").strip()
        status = _queue_status_label(str(row.get("status") or "pending_review"))
        section = "\n".join(
            [
                f"📝 {item_type}",
                f"ID: `{short_id}`",
                "",
                "Copy:",
                copy_text or "(vazio)",
                "",
                "Imagem:",
                image_url if image_url else "Sem imagem",
                "",
                "Status:",
                status,
                "",
                "Ações principais:",
                f"/approve {short_id}",
                f"/reject {short_id}",
                "",
                "Ações avançadas:",
                f"/preview {short_id}",
                f"/approve_now {short_id}",
            ]
        )
        sections.append(section)
    return "\n\n".join(sections)


def _render_status_reply(data: Dict[str, Any]) -> str:
    mode = str(data.get("mode") or "semi_autonomous")
    global_kill_switch = bool(data.get("global_kill_switch"))
    telegram_status = str(data.get("telegram_status") or "UNKNOWN").upper()
    channels = data.get("channels") if isinstance(data.get("channels"), dict) else {}
    enabled_channels = sorted([name for name, is_enabled in channels.items() if bool(is_enabled)])
    last_runs = data.get("last_runs") if isinstance(data.get("last_runs"), dict) else {}
    editorial = data.get("editorial_stock") if isinstance(data.get("editorial_stock"), dict) else {}
    recent_errors = data.get("recent_errors") if isinstance(data.get("recent_errors"), list) else []

    scheduler = "healthy"
    daily_post_run = last_runs.get("daily_post") if isinstance(last_runs.get("daily_post"), dict) else {}
    if str(daily_post_run.get("status") or "").strip().lower() == "failed":
        scheduler = "degraded"

    publishing = "enabled"
    if mode in {"manual", "containment"} or global_kill_switch:
        publishing = "disabled"
    elif "x" not in enabled_channels:
        publishing = "disabled"

    pending_review = int(editorial.get("pending_review_count") or 0)
    approved_scheduled = int(editorial.get("approved_scheduled_count") or 0)
    next_window = _format_hhmm_utc(editorial.get("next_window_utc"))
    coverage_days = float(editorial.get("coverage_days") or 0.0)

    risk_level = _status_risk_level(
        mode=mode,
        global_kill_switch=global_kill_switch,
        telegram_status=telegram_status,
        recent_errors=len(recent_errors),
    )

    return (
        "🔎 SYSTEM STATUS\n"
        "----------------\n"
        "\n"
        "Mode:\n"
        f"{mode}\n"
        "\n"
        "Scheduler:\n"
        f"{scheduler}\n"
        "\n"
        "Publishing:\n"
        f"{publishing}\n"
        "\n"
        "Queue:\n"
        f"Pending Review: {pending_review}\n"
        f"Approved Scheduled: {approved_scheduled}\n"
        "\n"
        "Next Window:\n"
        f"{next_window}\n"
        "\n"
        "Coverage:\n"
        f"{coverage_days:.2f} days\n"
        "\n"
        "Risk Level:\n"
        f"{risk_level}"
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
        next_action = "Proxima acao: rode /strategy_discover run e depois aprove candidatos com /strategy_discover queue."
        if status == "no_watchlist":
            next_action = "Proxima acao: rode /strategy_discover run para gerar candidatos da watchlist."
        return (
            "Scan de estrategia nao concluido.\n"
            f"status: {status}\n"
            f"watchlist: {watchlist_count}\n"
            f"{next_action}"
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


def _render_strategy_discovery_reply(data: Dict[str, Any]) -> str:
    status = str(data.get("status") or "unknown")
    if status in {"missing_x_oauth", "search_failed"}:
        return (
            "Descoberta de contas nao concluida.\n"
            f"status: {status}\n"
            "Proxima acao: valide OAuth do X e tente /strategy_discover run."
        )
    candidates = data.get("candidates") if isinstance(data.get("candidates"), list) else []
    criteria = data.get("criteria") if isinstance(data.get("criteria"), dict) else {}
    lines = [
        "Descoberta de contas concluida.",
        f"- Usuarios analisados: {int(data.get('scanned_users') or 0)}",
        f"- Novos candidatos: {int(data.get('discovered') or 0)}",
        f"- Candidatos atualizados: {int(data.get('updated') or 0)}",
        f"- Rejeitados por qualidade: {int(data.get('quality_rejected') or 0)}",
        f"- Removidos da fila por cutoff: {int(data.get('pruned_pending') or 0)}",
        f"- Pendentes para aprovacao: {int(data.get('pending_count') or 0)}",
    ]
    if criteria:
        lines.append(
            "Criterios ativos: "
            f"score>={int(criteria.get('min_score') or 0)}, "
            f"engaj>={float(criteria.get('min_avg_engagement') or 0):.1f}, "
            f"taxa>={float(criteria.get('min_engagement_rate_pct') or 0):.2f}%, "
            f"cadencia>={float(criteria.get('min_cadence_per_day') or 0):.2f}/dia, "
            f"sinais>={int(criteria.get('min_signal_posts') or 0)}, "
            f"posts>={int(criteria.get('min_recent_posts') or 0)}."
        )
    if candidates:
        top = candidates[0] if isinstance(candidates[0], dict) else {}
        lines.append(
            "Melhor candidato: "
            f"{top.get('account_username') or 'n/d'} "
            f"(score={int(top.get('score') or 0)}, id={top.get('candidate_id')})"
        )
        lines.append(f"URL: {top.get('profile_url') or 'n/d'}")
    lines.append("Proxima acao: /strategy_discover queue")
    return "\n".join(lines)


def _render_strategy_criteria_reply(data: Dict[str, Any]) -> str:
    criteria = data.get("criteria") if isinstance(data.get("criteria"), dict) else {}
    if not criteria:
        return "Criterios de descoberta indisponiveis."
    return (
        "Criterios de pre-selecao (modo rigoroso):\n"
        f"- Score minimo: {int(criteria.get('min_score') or 0)}\n"
        f"- Seguidores alvo: {int(criteria.get('min_followers') or 0)} a {int(criteria.get('max_followers') or 0)}\n"
        f"- Engajamento medio minimo: {float(criteria.get('min_avg_engagement') or 0):.1f}\n"
        f"- Taxa de engajamento minima: {float(criteria.get('min_engagement_rate_pct') or 0):.2f}%\n"
        f"- Cadencia minima: {float(criteria.get('min_cadence_per_day') or 0):.2f} post/dia\n"
        f"- Sinais minimos no search: {int(criteria.get('min_signal_posts') or 0)}\n"
        f"- Posts recentes minimos: {int(criteria.get('min_recent_posts') or 0)}\n"
        "Proxima acao: rode /strategy_discover run e depois /strategy_discover queue."
    )


def _render_strategy_candidates_queue(data: Dict[str, Any]) -> str:
    items = data.get("items") if isinstance(data.get("items"), list) else []
    if not items:
        return "Fila de candidatos de estrategia vazia.\nProxima acao: rode /strategy_discover run."

    criteria = data.get("criteria") if isinstance(data.get("criteria"), dict) else {}
    lines = [f"Candidatos de estrategia pendentes ({len(items)}):"]
    if criteria:
        lines.append(
            "Filtro ativo: "
            f"score>={int(criteria.get('min_score') or 0)}, "
            f"engaj>={float(criteria.get('min_avg_engagement') or 0):.1f}, "
            f"taxa>={float(criteria.get('min_engagement_rate_pct') or 0):.2f}%, "
            f"cadencia>={float(criteria.get('min_cadence_per_day') or 0):.2f}/dia."
        )
    for index, row in enumerate(items[:5], start=1):
        if not isinstance(row, dict):
            continue
        candidate_id = str(row.get("candidate_id") or "sem-id")
        username = str(row.get("account_username") or "n/d")
        user_id = str(row.get("account_user_id") or "n/d")
        score = int(row.get("score") or 0)
        followers = row.get("followers_count")
        avg_engagement = float(row.get("avg_engagement") or 0.0)
        cadence = float(row.get("cadence_per_day") or 0.0)
        engagement_rate_pct = float(row.get("engagement_rate_pct") or 0.0)
        signal_count = int(row.get("signal_post_count") or 0)
        selection_reason = str(row.get("selection_reason") or "").strip()
        lines.append(
            f"{index}. @{username} | user_id={user_id} | score={score} | followers={followers if followers is not None else 'n/d'}"
        )
        lines.append(
            "Metricas: "
            f"sinais={signal_count}, engaj={avg_engagement:.1f}, "
            f"taxa={engagement_rate_pct:.2f}%, cadencia={cadence:.2f}/dia"
        )
        lines.append(f"URL: {row.get('profile_url') or 'n/d'}")
        if selection_reason:
            lines.append(f"Racional: {selection_reason}")
        lines.append(f"Acoes: /strategy_discover approve {candidate_id} | /strategy_discover reject {candidate_id}")
    return "\n".join(lines)


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
    elif response.message == "mode_ok":
        body = (
            "Modo operacional atual:\n"
            f"- Modo: {data.get('mode')}\n"
            "Para alterar: /mode set <manual|semi_autonomous|autonomous_limited|containment> [confirm]"
        )
    elif response.message == "mode_updated":
        body = (
            "Modo operacional atualizado.\n"
            f"- Novo modo: {data.get('mode')}\n"
            f"- Alterado em: {data.get('last_mode_change_at') or 'n/d'}"
        )
    elif response.message == "mode_set_requires_confirmation":
        body = (
            "Transicao para autonomous_limited requer confirmacao explicita.\n"
            f"Execute: {data.get('hint') or '/mode set autonomous_limited confirm'}"
        )
    elif response.message == "mode_invalid_target":
        body = "Modo invalido. Use /mode para listar os modos aceitos."
    elif response.message == "mode_invalid_args":
        body = "Uso: /mode ou /mode set <modo> [confirm]."
    elif response.message == "stability_report_ok":
        body = _render_stability_alert(data, containment_mode=False)
    elif response.message == "stability_auto_containment_applied":
        body = _render_stability_alert(data, containment_mode=True)
    elif response.message == "stability_kill_switch_applied":
        body = _render_stability_alert(data, containment_mode=True)
    elif response.message == "stability_containment_applied":
        body = _render_stability_alert(data, containment_mode=True)
    elif response.message == "stability_containment_not_required":
        body = _render_stability_alert(data, containment_mode=True)
    elif response.message == "stability_containment_requires_admin":
        body = _render_alert(
            alert_type="Containment Authorization",
            workspace=str(data.get("workspace_id") or "revfirst"),
            action="Containment command blocked for current user.",
            required="Use owner/admin account.",
        )
    elif response.message == "stability_invalid_args":
        body = _render_alert(
            alert_type="Command Usage",
            workspace=str(data.get("workspace_id") or "revfirst"),
            action="Invalid stability command arguments.",
            required="/stability ou /stability contain",
        )
    elif response.message == "kill_switch_acknowledged":
        body = (
            "Kill-switch global reconhecido.\n"
            f"- TTL estendido para: {data.get('ttl_seconds')}s\n"
            "Proxima acao: corrigir causas-raiz antes de retomar operacao."
        )
    elif response.message == "kill_switch_not_enabled":
        body = "Kill-switch global nao esta ativo no momento."
    elif response.message == "kill_switch_requires_owner":
        body = "Apenas owner pode reconhecer o kill-switch global."
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
    elif response.message == "strategy_discovery_ok":
        body = _render_strategy_discovery_reply(data)
    elif response.message == "strategy_discovery_not_ready":
        body = _render_strategy_discovery_reply(data)
    elif response.message == "strategy_discovery_criteria":
        body = _render_strategy_criteria_reply(data)
    elif response.message == "strategy_candidates_queue":
        body = _render_strategy_candidates_queue(data)
    elif response.message == "strategy_candidates_queue_empty":
        body = _render_strategy_candidates_queue(data)
    elif response.message == "strategy_watchlist_updated":
        body = (
            "Conta adicionada na watchlist de estrategia.\n"
            f"account_user_id: {data.get('account_user_id')}\n"
            f"username: {data.get('account_username') or 'n/d'}\n"
            "Proxima acao: /strategy_scan run"
        )
    elif response.message == "strategy_candidate_approved":
        body = (
            "Candidato aprovado e movido para watchlist.\n"
            f"candidate_id: {data.get('candidate_id')}\n"
            f"account_user_id: {data.get('account_user_id')}\n"
            f"username: {data.get('account_username') or 'n/d'}\n"
            "Proxima acao: /strategy_scan run"
        )
    elif response.message == "strategy_candidate_rejected":
        body = (
            "Candidato rejeitado com sucesso.\n"
            f"candidate_id: {data.get('candidate_id')}\n"
            f"account_user_id: {data.get('account_user_id')}\n"
            f"username: {data.get('account_username') or 'n/d'}"
        )
    elif response.message == "strategy_candidate_missing_id":
        body = "Informe candidate_id. Exemplo: /strategy_discover approve <candidate_id>."
    elif response.message == "strategy_candidate_not_found":
        body = "Candidate_id nao encontrado. Rode /strategy_discover queue para listar pendentes."
    elif response.message == "strategy_discovery_invalid_args":
        body = (
            "Uso invalido de strategy_discover.\n"
            "Exemplos: /strategy_discover run | /strategy_discover criteria | /strategy_discover queue | "
            "/strategy_discover approve <candidate_id> | /strategy_discover reject <candidate_id>."
        )
    elif response.message == "strategy_report_empty":
        body = "Relatorio de estrategia ainda vazio. Rode /strategy_scan run primeiro."
    elif response.message == "strategy_scan_not_ready":
        body = _render_strategy_scan_reply(data)
    elif response.message == "approved_and_published":
        body = _render_publish_confirmation(data)
    elif response.message == "approved_scheduled":
        body = _render_approval_confirmation(data)
    elif response.message == "approve_publish_failed":
        body = _render_publish_failure_alert(data)
    elif response.message == "queue_item_rejected_regenerated":
        body = _render_reject_confirmation(data, regenerated=True)
    elif response.message == "queue_item_rejected":
        body = _render_reject_confirmation(data, regenerated=False)
    elif response.message == "no_pending_queue_item":
        body = "Nao ha item pendente para aprovar. Use /queue para verificar."
    elif response.message == "missing_queue_id":
        body = "Informe o queue_id. Exemplo: /approve <queue_id>, /approve_now <queue_id> ou /preview <queue_id>."
    elif response.message == "queue_id_ambiguous":
        candidates = data.get("candidates") if isinstance(data.get("candidates"), list) else []
        body = (
            "Queue ID curto ambiguo.\n"
            f"Candidatos: {', '.join(str(value) for value in candidates) if candidates else 'n/d'}\n"
            "Use o queue_id completo."
        )
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
