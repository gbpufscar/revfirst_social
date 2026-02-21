# Plano de Correcao de Gaps Criticos para Integracao Oficial no X (V2)

Data base: 2026-02-20  
Status: Proposto para execucao imediata

## Objetivo

Fechar os gaps impeditivos para operacao oficial no X com governanca, onboarding OAuth completo, robustez transacional de publicacao, remocao de legado e cobertura de testes dos fluxos reais.

## Gaps impeditivos (escopo deste plano)

1. Bypass de governanca fora de `/publishing/*` via `daily-post` com `auto_publish=true`.
2. Onboarding oficial de conta X incompleto (authorize/callback/state/PKCE e validacao de identidade/escopo).
3. Tratamento de erros de publicacao incompleto, com risco de estado ambiguo de fila.
4. Stack legado ainda presente no repositorio (`orchestrator/` + `pipelines/`).
5. Cobertura de testes sem fechamento do onboarding OAuth real.

## Cronograma por fases

1. Fase 0: 2026-02-23
2. Fase 1: 2026-02-24 a 2026-02-25
3. Fase 2: 2026-02-26 a 2026-03-03
4. Fase 3: 2026-03-04 a 2026-03-07
5. Fase 4: 2026-03-10 a 2026-03-11
6. Fase 5: 2026-03-12 a 2026-03-14
7. Fase 6 (staging): 2026-03-17 a 2026-03-19
8. Fase 7 (producao + janela de 30 dias): 2026-03-20 a 2026-04-18

## Fase 0 - Decisao de arquitetura e baseline

### Objetivo

Congelar decisao de governanca e baseline tecnico antes das mudancas.

### Entregaveis

1. ADR curta com politica final para `auto_publish`.
2. Snapshot de baseline (testes, metrics, comportamento atual).
3. Checklist de aceite por fase.

### Gate de saida

Decisao aprovada: publicacao oficial no X somente por caminho governado (fila + aprovacao), sem caminho paralelo em API publica.

## Fase 1 - Fechamento do bypass de governanca

### Objetivo

Eliminar publicacao direta no X fora do caminho oficial de aprovacao.

### Mudancas tecnicas

1. Bloquear `auto_publish=true` para role `member` em `/daily-post/generate`.
2. Exigir guarda interna equivalente a `/publishing/*` quando `auto_publish=true` (flag + chave interna), ou remover auto publish da rota publica.
3. Garantir que o fluxo oficial continue sendo fila + `/approve`.

### Arquivos alvo

1. `src/daily_post/router.py`
2. `src/daily_post/service.py`
3. `tests/test_telegram_seed_daily_post.py`
4. `tests/control/test_queue_approve.py` (regressao de fluxo oficial)

### Testes minimos

1. `member` nao publica com `auto_publish=true`.
2. `owner/admin` so publicam com guardas corretas.
3. Fluxo oficial via fila e `/approve` permanece funcional.

### Gate de saida

Zero bypass de governanca no runtime.

## Fase 2 - Onboarding OAuth oficial da conta X

### Objetivo

Completar fluxo OAuth oficial ponta a ponta no backend.

### Mudancas tecnicas

1. Criar endpoint de inicio OAuth (`authorize`) com `state` e PKCE (`code_verifier` + `code_challenge`).
2. Criar callback backend com validacao de `state` (expiracao, uso unico, anti-replay).
3. Executar exchange server-side de forma segura.
4. Validar escopo minimo para publish (`tweet.write` obrigatorio).
5. Validar identidade da conta conectada (ex.: `/2/users/me`) e persistir `x_user_id`, `x_username`, escopos efetivos.
6. Expandir status de conexao para refletir aptidao real de publicacao oficial.

### Arquivos alvo

1. `src/integrations/x/router.py`
2. `src/integrations/x/service.py`
3. `src/integrations/x/x_client.py`
4. `src/schemas/integrations_x.py`
5. `src/storage/models.py`
6. `migrations/versions/*` (nova migration para metadados da conta conectada)

### Testes minimos

1. `authorize` gera `state`/PKCE validos.
2. callback com `state` invalido/expirado/replay falha.
3. callback com escopo sem `tweet.write` falha com motivo claro.
4. callback com sucesso persiste token + identidade da conta + escopo.

### Gate de saida

Conta X oficial conectada por fluxo completo e validada para post real.

## Fase 3 - Robustez de erro e consistencia transacional

### Objetivo

Garantir que falhas de transporte/parsing nao deixem estado ambiguo de fila nem gerem duplicidade sem controle.

### Mudancas tecnicas

1. Normalizar excecoes de transporte/timeout/parsing no `x_client` para `XClientError`.
2. Ampliar tratamento de excecoes no publish para cobrir falhas inesperadas com auditoria e metrica.
3. Ajustar estado de fila para evitar commit definitivo em `approved` antes da tentativa de publicacao.
4. Introduzir estado intermediario (`publishing`) ou transacao equivalente.
5. Reforcar idempotencia de envio para retries seguros.

### Arquivos alvo

1. `src/integrations/x/x_client.py`
2. `src/publishing/service.py`
3. `src/control/services.py`
4. `src/control/handlers/approve.py`
5. `src/control/handlers/run.py`

### Testes minimos

1. Excecao de rede vira erro controlado e auditado.
2. Excecao de JSON invalido vira erro controlado e auditado.
3. Falha no publish nao deixa item em estado ambiguo.
4. Retry nao duplica envio quando ja houve tentativa registrada.

### Gate de saida

Fila consistente e auditavel em cenarios de falha.

## Fase 4 - Remocao efetiva da stack legada (Sprint 2 do WP-5)

### Objetivo

Eliminar risco residual de execucao por caminho antigo.

### Mudancas tecnicas

1. Remover `pipelines/` legado.
2. Remover wrapper legado `orchestrator/manager.py`.
3. Migrar testes legados para stack canonica `src/orchestrator`.
4. Endurecer CI para impedir reintroducao de imports/entrypoints legados.

### Arquivos alvo

1. `pipelines/*` (remocao)
2. `orchestrator/manager.py` (remocao)
3. `tests/test_vertical_pipeline.py` (migracao)
4. `.github/workflows/ci.yml`
5. `scripts/check_canonical_orchestrator.sh`

### Testes minimos

1. Scheduler canonico continua verde.
2. Sem referencia em codigo/teste para legado.
3. CI falha ao detectar reintroducao de caminho legado.

### Gate de saida

Unico caminho operacional: `python -m src.orchestrator.manager`.

## Fase 5 - Cobertura de testes do onboarding real

### Objetivo

Fechar lacunas de regressao nos fluxos de conexao oficial da conta X.

### Mudancas tecnicas

1. Adicionar testes para `/integrations/x/oauth/exchange` (sucesso + erro).
2. Adicionar testes para novo fluxo `authorize/callback/state`.
3. Adicionar testes de governanca para `daily-post` com `auto_publish`.
4. Adicionar testes de erro nao-`XClientError` em publish/control-plane.

### Arquivos alvo

1. `tests/test_integration_x_oauth.py`
2. `tests/test_telegram_seed_daily_post.py`
3. `tests/test_publishing_engine.py`
4. `tests/control/test_queue_approve.py`
5. `tests/control/test_router.py`

### Gate de saida

Cobertura critica fechada para onboarding e publish oficial no X.

## Fase 6 - Homologacao em staging

### Objetivo

Validar fluxo E2E real em ambiente de homologacao.

### Checklist

1. Conectar conta X oficial por fluxo novo.
2. Rodar ingestao e gerar item de fila.
3. Aprovar item e publicar no X.
4. Verificar auditoria, uso, metrics e status de conexao.
5. Simular falhas de token/rede e validar recuperacao.

### Gate de saida

Checklist de homologacao 100% aprovado.

## Fase 7 - Producao + validacao operacional de 30 dias

### Objetivo

Comprovar estabilidade operacional com evidencia diaria.

### Rotina diaria minima

1. Health checks (`/health`, `/version`, `/metrics`).
2. Verificacao de status OAuth da conta oficial.
3. Ingestao, proposta, aprovacao e publicacao por caminho oficial.
4. Revisao de erros e alertas.
5. Registro no log operacional diario.

### Evidencia

Usar: `docs/OPERATIONAL_VALIDATION.md` com 1 entrada por dia, sem estimativas.

### Gate final

1. 30 dias completos com evidencia diaria.
2. Zero bypass de governanca.
3. Zero incidente critico sem tratamento.
4. Zero divergencia critica entre auditoria e metricas.
5. Zero execucao de stack legado.

## Mapa Gap -> Fase

1. Bypass de governanca -> Fase 1
2. Onboarding OAuth oficial incompleto -> Fase 2
3. Robustez de erro e estado de fila -> Fase 3
4. Stack legado residual -> Fase 4
5. Cobertura de testes onboarding real -> Fase 5

## Criterios de Go/No-Go

`GO` somente se:

1. Fases 1 a 6 concluidas e aprovadas.
2. Secrets de producao completos e validados.
3. Janela de 30 dias (Fase 7) concluida com evidencia.
4. Sem incidentes criticos abertos relacionados a token, publish ou governanca.

`NO-GO` se qualquer criterio acima falhar.
