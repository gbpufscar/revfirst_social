# Plano Completo de Correção e Implementação (Agents + Autonomia X)

Data: 2026-02-21  
Base: `/Users/gbp.ufscar/Desktop/Antigravity/Automacoes/RevFirst_Social/docs/DIAGNOSTICO_AGENTS_E_NOVOS_AGENTES_2026-02-21.md`

## 1) Objetivo

Fechar todos os gaps técnicos do diagnóstico para sair de operação assistida para operação integrada, governada e progressivamente autônoma no X, com:
- pipeline automático confiável;
- governança sem bypass;
- observabilidade e métricas de crescimento;
- 2 novos agents estratégicos:
  - analisador de performance/crescimento da conta X;
  - minerador de estratégia de contas com crescimento acelerado.

## 2) Princípios de Execução

- Segurança e governança primeiro (nenhuma publicação fora de controle).
- Mudanças incrementais com rollback simples.
- Toda fase com DoD (Definition of Done) e testes mínimos.
- Deploy por ondas: staging -> produção.
- Operação diária com evidência em log/auditoria.

## 3) Macrocronograma por Fases

- Fase 0: Hardening crítico imediato.
- Fase 1: Consolidação da autonomia operacional (scheduler + fila).
- Fase 2: Agent de performance e crescimento da conta X.
- Fase 3: Agent de estratégia de contas benchmark.
- Fase 4: Loop de otimização autônoma (decisão orientada por dados).
- Fase 5: Estabilização, operação de 30 dias e prontidão oficial.

---

## Fase 0 - Hardening Crítico (Bloqueadores)

### Objetivo
Eliminar risco de operação incorreta, bypass de governança e inconsistência de execução.

### Escopo
1. Corrigir bug do scheduler no cringe guard.
2. Fechar bypass de publicação direta fora da fila.
3. Unificar entrypoint canônico do orquestrador em artefatos operacionais.
4. Garantir compatibilidade entre `origin/main` e código local já ajustado.

### Tarefas técnicas
1. Scheduler:
- Padronizar validação de cringe para aceitar contrato atual (`cringe`) e legado (`passed`) em:
  - `/Users/gbp.ufscar/Desktop/Antigravity/Automacoes/RevFirst_Social/src/orchestrator/pipeline.py`

2. Governança de publish direto:
- Restringir `/publishing/post` e `/publishing/reply` para `owner/admin`.
- Exigir `X-RevFirst-Internal-Key` (flag + segredo) para uso direto.
- Arquivo:
  - `/Users/gbp.ufscar/Desktop/Antigravity/Automacoes/RevFirst_Social/src/publishing/router.py`

3. EntryPoint canônico:
- Garantir `ExecStart=/usr/bin/python3 -m src.orchestrator.manager`.
- Arquivo:
  - `/Users/gbp.ufscar/Desktop/Antigravity/Automacoes/RevFirst_Social/deploy/systemd/revfirst_social.service`

4. Guardrails CI/operação:
- Manter script de validação do caminho canônico no pipeline.
- Arquivo:
  - `/Users/gbp.ufscar/Desktop/Antigravity/Automacoes/RevFirst_Social/scripts/check_canonical_orchestrator.sh`

### Testes mínimos
- `tests/test_vertical_pipeline.py`
- `tests/test_publishing_engine.py`
- novos/ajustes para garantir:
  - `member` não publica direto;
  - rota direta sem chave interna retorna 403;
  - scheduler não quebra com contrato cringe.

### DoD
- Nenhum caminho público publica no X sem governança aprovada.
- Scheduler executa sem erro de contrato do cringe.
- CI verde com validação canônica de orquestrador.

---

## Fase 1 - Consolidação da Autonomia Operacional

### Objetivo
Transformar scheduler em pipeline automático real que alimente fila continuamente sem perder governança.

### Escopo
1. Ativar auto-queue de replies elegíveis.
2. Ativar auto-queue de daily post com intervalo configurável.
3. Preservar publicação somente via aprovação (`/approve`).
4. Garantir idempotência forte de enqueue.

### Tarefas técnicas
1. Configuração:
- Adicionar e validar flags:
  - `SCHEDULER_AUTO_QUEUE_REPLIES_ENABLED`
  - `SCHEDULER_AUTO_QUEUE_DAILY_POST_ENABLED`
  - `SCHEDULER_DAILY_POST_INTERVAL_HOURS`
- Arquivo:
  - `/Users/gbp.ufscar/Desktop/Antigravity/Automacoes/RevFirst_Social/src/core/config.py`

2. Scheduler pipeline:
- Unificar `src/orchestrator/pipeline.py` para:
  - ingestão;
  - avaliação;
  - enqueue de replies;
  - enqueue de daily post (respeitando janela de intervalo);
  - geração opcional de imagem para canais.

3. Fila e idempotência:
- Garantir `idempotency_key` por candidato e por draft de daily post.
- Reforçar deduplicação em:
  - `/Users/gbp.ufscar/Desktop/Antigravity/Automacoes/RevFirst_Social/src/control/services.py`

4. Observabilidade:
- Incluir métricas de `queued_reply_candidates` e `daily_post_queue.status` no payload de execução do scheduler.

### Testes mínimos
- `tests/test_vertical_pipeline.py`
- `tests/test_orchestrator_scheduler.py`
- `tests/control/test_queue_approve.py`

### DoD
- Scheduler produz itens pendentes automaticamente sem publicar sozinho.
- `/queue` mostra itens novos de replies e daily post.
- Aprovação manual publica com sucesso e mantém trilha de auditoria.

---

## Fase 2 - Novo Agent: Performance e Crescimento da Conta X

### Objetivo
Criar inteligência de métricas de conta/post para orientar decisão de conteúdo e cadência.

### Escopo funcional
- Snapshot diário de estado da conta X.
- Snapshot de performance dos posts publicados.
- KPIs de crescimento e eficiência.
- Relatórios Telegram acionáveis (`/growth`, `/growth_weekly`).

### Modelo de dados (nova migration)
Criar tabelas:
1. `x_account_snapshots`
- workspace_id, account_user_id, account_username
- followers_count, following_count, tweet_count, listed_count
- captured_at

2. `x_post_metrics_snapshots`
- workspace_id, external_post_id
- like_count, reply_count, repost_count, quote_count, impression_count (se disponível)
- captured_at

3. `x_growth_insights`
- workspace_id, period_type (daily/weekly)
- kpis_json, recommendations_json
- created_at

### Tarefas técnicas
1. Cliente X (read endpoints):
- ampliar `x_client.py` com métodos de leitura de métricas de conta/posts.

2. Service do agent:
- criar:
  - `/Users/gbp.ufscar/Desktop/Antigravity/Automacoes/RevFirst_Social/src/analytics/x_performance_agent.py`
- responsabilidades:
  - coletar snapshots;
  - calcular KPIs:
    - crescimento líquido de seguidores;
    - taxa de engajamento por post;
    - consistência de cadência;
    - taxa de aprovação/publicação;
  - gerar recomendações de ação.

3. Exposição no control plane:
- novos handlers:
  - `/growth`
  - `/growth_weekly`
- integração com formato humano no Telegram.

4. Scheduler:
- execução automática diária do coletor de performance.

### Testes mínimos
- tests unit do agent de KPI.
- tests de integração do comando Telegram.
- tests de persistência dos snapshots.

### DoD
- KPIs diários e semanais disponíveis por comando.
- Recomendações acionáveis com base em dados recentes.
- Histórico mínimo de snapshots persistido e auditável.

---

## Fase 3 - Novo Agent: Estratégia de Contas Benchmark

### Objetivo
Detectar padrões de crescimento de contas referência e traduzir para playbooks aplicáveis à RevFirst (sem copiar conteúdo literal).

### Escopo funcional
- Watchlist de contas alvo.
- Coleta recorrente de posts/perfil dessas contas.
- Extração de padrões de:
  - frequência;
  - sequência de formatos (post/reply/thread);
  - estilo de hook/copy/CTA;
  - uso de imagem.
- Recomendação semanal de ajustes no playbook RevFirst.

### Modelo de dados (nova migration)
Criar tabelas:
1. `x_strategy_watchlist`
- workspace_id, account_user_id, account_username, status, added_at

2. `x_competitor_posts`
- workspace_id, watched_account_user_id, post_id, text, created_at
- métricas principais, has_image, post_type

3. `x_strategy_patterns`
- workspace_id, period_window
- pattern_json, confidence, generated_at

4. `x_strategy_recommendations`
- workspace_id, recommendation_json, rationale_json, created_at

### Tarefas técnicas
1. Agent service:
- criar:
  - `/Users/gbp.ufscar/Desktop/Antigravity/Automacoes/RevFirst_Social/src/strategy/x_growth_strategy_agent.py`

2. Algoritmos iniciais (versão mínima):
- cluster por faixa de horário.
- distribuição de formatos.
- padrões de abertura de copy (n-grams simples).
- correlação simples entre padrão e engajamento relativo.

3. Comandos Telegram:
- `/strategy_scan`
- `/strategy_report`

4. Governança ética/brand:
- nunca replicar texto integral de terceiros.
- produzir apenas abstrações táticas e templates internos.

### Testes mínimos
- parsing e normalização de posts benchmark.
- extração de padrões em dataset sintético.
- geração de recomendações sem plágio literal.

### DoD
- Watchlist ativa e escaneável.
- Relatório de padrões gerado semanalmente.
- Recomendações aplicáveis ao planejamento de conteúdo.

---

## Fase 4 - Loop de Otimização Autônoma

### Objetivo
Fechar ciclo: medir -> decidir -> ajustar execução automaticamente (mantendo aprovação humana para publicação).

### Escopo
1. Motor de decisão de parâmetros operacionais:
- limiar de oportunidade;
- cadência de daily post;
- priorização de tipo de conteúdo;
- janelas de horário.

2. Integração entre agents:
- `x_performance_agent` + `x_growth_strategy_agent` alimentam configuração recomendada.

3. Aplicação controlada:
- mudanças primeiro como sugestão.
- opção de aplicação automática só com flag explícita e auditoria.

### Tarefas técnicas
1. Criar service:
- `/Users/gbp.ufscar/Desktop/Antigravity/Automacoes/RevFirst_Social/src/strategy/optimizer.py`

2. Criar tabela:
- `x_strategy_decisions`
- registrar decisão, origem (performance/benchmark), impacto esperado, status.

3. Commands Telegram:
- `/optimizer_status`
- `/optimizer_apply <decision_id>`

### Testes mínimos
- decisão determinística em cenários de entrada controlados.
- sem alteração automática quando flag desligada.

### DoD
- Existe trilha completa de recomendação -> decisão -> execução.
- Mudanças de estratégia são auditáveis e reversíveis.

---

## Fase 5 - Estabilização e Operação Assistida (30 dias)

### Objetivo
Comprovar estabilidade operacional e prontidão para operação oficial contínua.

### Escopo
1. Checklist operacional diário por 30 dias.
2. SLOs de confiabilidade e governança.
3. Revisão semanal de resultados e ajustes finos.

### Rotina diária (D+1 a D+30)
1. Verificar `/health`, `/metrics`, `/integrations/x/oauth/status/{workspace_id}`.
2. Validar execução scheduler e presença de novos itens na fila.
3. Revisar `/queue` + `/preview` e aprovar/publicar.
4. Rodar `/growth` e registrar evolução.
5. Rodar `/strategy_report` (ou semanalmente) e registrar ações.
6. Conferir erros críticos em `publish_audit_logs` e `admin_actions`.

### SLOs sugeridos
- Disponibilidade API: >= 99.5%
- Falha de publicação por erro técnico: < 5%
- Publicação fora da governança: 0 ocorrências
- Execuções de scheduler com erro crítico: 0 por semana

### DoD
- 30 dias com evidência de operação consistente.
- Nenhum bypass de governança.
- Crescimento e desempenho acompanhados por métricas históricas.

---

## 4) Sequência de Entrega Recomendada (PRs)

PR-1: Hardening crítico (Fase 0)  
PR-2: Scheduler autônomo com fila (Fase 1)  
PR-3: Agent performance + migration (Fase 2)  
PR-4: Agent benchmark + migration (Fase 3)  
PR-5: Optimizer + decisões auditáveis (Fase 4)  
PR-6: Operação 30 dias + documentação final (Fase 5)

## 5) Riscos e Mitigações

1. Limite/plano da API X insuficiente para coleta avançada
- Mitigação: versão mínima baseada em dados já disponíveis + coleta progressiva.

2. Ruído nas recomendações dos novos agents
- Mitigação: confidence score + revisão humana antes de aplicar.

3. Regressão no fluxo de publicação
- Mitigação: testes de regressão e rollout por fases com canário.

4. Drift entre local e produção
- Mitigação: PRs pequenos, CI estrita, checklist de deploy e migration.

## 6) Critério Final de Prontidão Oficial

Pronto para operação oficial contínua quando:
1. Fase 0-4 concluídas e em produção.
2. 30 dias da Fase 5 concluídos com SLO atendido.
3. Evidência de crescimento monitorado + decisões estratégicas auditáveis.
4. Fluxo de governança mantido (aprovação antes da publicação) sem exceções.

