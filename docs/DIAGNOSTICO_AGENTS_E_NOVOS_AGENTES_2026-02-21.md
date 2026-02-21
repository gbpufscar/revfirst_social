# Diagnóstico Completo de Agents e Autonomia (X + Telegram)

Data: 2026-02-21
Base analisada: `origin/main` (estado mais próximo do deploy), com observação de diferenças locais não publicadas.

## 1) Resumo Executivo

O sistema já está funcional para operação oficial assistida no X:
- OAuth oficial com callback + PKCE + state + validação de escopo e identidade da conta.
- Publicação real no X validada via fila/aprovação no Telegram.
- Fluxo de aprovação com preview de copy e imagem no chat.

Porém, ainda não está plenamente autônomo no sentido de "rodar sozinho ponta-a-ponta com estratégia adaptativa":
- O scheduler em `origin/main` ainda é parcial e possui bug de chave no cringe guard.
- A governança ainda pode ser contornada fora do fluxo de fila (endpoint de publishing direto para `member`).
- Não existem os 2 agentes estratégicos que você pediu (análise de performance/crescimento e mineração de estratégia de contas referência).
- Não existe loop fechado de otimização estratégica baseado em métricas de crescimento de conta.

## 2) Agents Existentes e Status

### 2.1 Agents de domínio (implementados)
- Reply Writer: `src/domain/agents/reply_writer.py`
- Brand Consistency: `src/domain/agents/brand_consistency.py`
- Anti-Cringe Guard: `src/domain/agents/anti_cringe_guard.py`
- Thread Detector: `src/domain/agents/thread_detector.py`
- Lead Tracker: `src/domain/agents/lead_tracker.py`
- Composição dos agents: `src/domain/agents/pipeline.py`

Status:
- Codificados e testados em unidade.
- Lógica majoritariamente heurística/regras (não há motor adaptativo de estratégia).

### 2.2 Agents de operação/conteúdo (implementados)
- Daily Post Writer (com seeds): `src/daily_post/service.py`
- Routing multicanal: `src/domain/routing/channel_router.py`
- Image Agent/infra de mídia: `src/media/service.py`
- Reporting Agent (daily/weekly): `src/reporting/service.py`

Status:
- Funcionais para geração, roteamento, fila e relatórios operacionais.
- Reporting atual é operacional, não é analisador profundo de crescimento da conta X.

### 2.3 Agents de controle/publicação (implementados)
- Command Center Telegram: `src/control/telegram_bot.py`, `src/control/command_router.py`
- Handlers `/run`, `/queue`, `/preview`, `/approve`: `src/control/handlers/*.py`
- Engine de publicação: `src/publishing/service.py`

Status:
- Fluxo assistido está funcionando em produção (run -> queue -> preview -> approve -> publish).

### 2.4 Integração oficial X (implementada)
- OAuth authorize/exchange/callback/status/revoke: `src/integrations/x/router.py`
- Estado OAuth + refresh com rotação: `src/integrations/x/service.py`
- Cliente X (token exchange/refresh/users/me/publish/search): `src/integrations/x/x_client.py`

Status:
- Pronto e validado para conta oficial conectada com `tweet.write`.

## 3) Lacunas Técnicas Reais (Impedem autonomia robusta)

### 3.1 Scheduler em `origin/main` está incompleto para autonomia total
Evidência:
- `src/orchestrator/pipeline.py` (origin/main) executa ingestão + avaliação, mas não fecha ciclo completo de fila/publicação.

Impacto:
- Dependência de comando manual para parte relevante do funil.

### 3.2 Bug no scheduler (crítico)
Evidência:
- `src/orchestrator/pipeline.py` (origin/main) usa `bundle["cringe_guard"]["passed"]`, porém o contrato do cringe usa `cringe` (`src/domain/agents/contracts.py`).

Impacto:
- Pode quebrar execução do scheduler com erro em runtime.

### 3.3 Bypass de governança fora da fila
Evidência:
- `src/publishing/router.py` em `origin/main` ainda permite `member` publicar em `/publishing/post` e `/publishing/reply`.

Impacto:
- Publicação pode ocorrer fora do controle de aprovação no Telegram.

### 3.4 Orquestração canônica com inconsistência operacional
Evidência:
- `scripts/check_canonical_orchestrator.sh` exige `python -m src.orchestrator.manager`.
- `deploy/systemd/revfirst_social.service` em `origin/main` ainda está com `python3 -m orchestrator.manager`.

Impacto:
- Risco de operação por entrypoint errado em ambientes com systemd.

### 3.5 Ausência dos 2 novos agents estratégicos
Evidência:
- Não há código/tabelas/rotas para:
  - análise de crescimento da conta X;
  - mineração de estratégia de contas referência.

Impacto:
- Sem aprendizado estratégico contínuo e sem benchmark competitivo estruturado.

## 4) Diferenças Locais Não Publicadas (Importante)

No seu workspace local há mudanças não commitadas que já atacam parte da autonomia:
- `src/orchestrator/pipeline.py` local: inclui auto-queue de replies + daily_post.
- `src/core/config.py` local: flags `scheduler_auto_queue_*` e intervalo de daily post.
- `src/publishing/router.py` local: restringe publish direto (owner/admin + key interna).
- `deploy/systemd/revfirst_social.service` local: corrigido para `src.orchestrator.manager`.

Conclusão:
- Parte do que você quer já está parcialmente codificado localmente, mas ainda não está no `origin/main`/deploy.

## 5) Novos Agents Necessários

## 5.1 Agent A: Analisador de Performance e Crescimento da Conta X

Objetivo:
- Medir evolução real da conta e da execução (crescimento, eficiência de post, tendências por formato/horário).

Entradas:
- `publish_audit_logs`, `workspace_daily_usage`, `approval_queue_items`.
- API X para snapshot de conta e métricas de posts (conforme permissões do app/plano).

Saídas:
- Relatório diário e semanal de crescimento.
- Alertas: queda de desempenho, baixa cadência, aumento de erro.
- Recomendações acionáveis para próxima janela de postagens.

Implementação proposta:
- Novo módulo: `src/analytics/x_performance_agent.py`
- Novas tabelas:
  - `x_account_snapshots`
  - `x_post_metrics_snapshots`
  - `x_growth_insights`
- Novos comandos Telegram:
  - `/growth`
  - `/growth_weekly`

## 5.2 Agent B: Detector e Minerador de Estratégia de Contas de Crescimento Rápido

Objetivo:
- Identificar padrões de contas que crescem rápido e transformar em playbooks internos (sem cópia literal).

Entradas:
- Lista de contas alvo/watchlist.
- Posts/replies públicos dessas contas em janela móvel.

Saídas:
- Sequência de estratégia:
  - frequência
  - formato (post único/thread/reply)
  - estilo de hook/copy/CTA
  - padrão de uso de imagem
- Regras/insights para ajustar o gerador de conteúdo da RevFirst.

Implementação proposta:
- Novo módulo: `src/strategy/x_growth_strategy_agent.py`
- Novas tabelas:
  - `x_strategy_watchlist`
  - `x_competitor_posts`
  - `x_strategy_patterns`
  - `x_strategy_recommendations`
- Novo comando Telegram:
  - `/strategy_scan`

## 6) Plano de Fechamento por Fases

### Fase 0 (Hardening imediato)
1. Corrigir bug `cringe_guard` no scheduler de produção.
2. Publicar restrição de governança no `/publishing/*` (owner/admin + chave interna).
3. Unificar entrypoint canônico (`src.orchestrator.manager`) em artefatos operacionais.
4. Publicar flags de autonomia do scheduler (replies + daily_post intervalado).

### Fase 1 (Agent de performance)
1. Criar migrações das tabelas de snapshot/insight.
2. Implementar coleta diária de conta/posts.
3. Implementar cálculo de KPIs de crescimento e eficiência.
4. Expor `/growth` e `/growth_weekly` no Telegram.

### Fase 2 (Agent de estratégia de contas referência)
1. Modelagem de watchlist e ingestão de contas alvo.
2. Coleta e normalização de posts/perfis alvo.
3. Extração de padrões de sequência/frequência/copy/mídia.
4. Geração de recomendações estratégicas para conteúdo RevFirst.

### Fase 3 (Integração autônoma de verdade)
1. Encadear scheduler para:
   - ingest_open_calls
   - propose_replies
   - queue_daily_post (com intervalo)
   - refresh/performance/strategy scans
2. Disparar resumo diário no Telegram com ações sugeridas.
3. Manter publicação final em modo governado (`/approve`) para segurança de marca.

## 7) Critérios de Pronto para "integrado e autônomo"

1. Scheduler sem erro em execução contínua (7+ dias).
2. Fila sendo alimentada automaticamente (replies + daily post).
3. Publicação exclusivamente via governança aprovada.
4. Relatórios de crescimento da conta X com snapshots diários.
5. Recomendações estratégicas semanais baseadas em contas benchmark.
6. Ajuste de estratégia documentado e auditável (inputs -> decisão -> resultado).

## 8) Prioridade Recomendada

1. Fase 0 (obrigatória, imediata).
2. Agent A (performance/crescimento).
3. Agent B (estratégia benchmark).
4. Fechamento do loop autônomo completo no scheduler.

