# Plano Mestre de Execucao - Fechamento de Gaps Criticos (X)

Data base: 2026-02-20  
Status: Em execucao (WP-1, WP-2, WP-3, WP-4 e WP-5 concluidas em 2026-02-20)

## Objetivo

Fechar os gaps criticos para operacao oficial no X com governanca, confiabilidade de token, metricas corretas e operacao sem ambiguidade de stack, seguido de validacao operacional por 30 dias corridos.

## Baseline atual

- Suite local de testes (apos fechamento de secrets): `111 passed`.
- Fase 11 ainda `IN_PROGRESS`; fases 17 e 18 ainda `NOT_STARTED`.
- Gaps criticos em aberto:
- nenhum dos 4 gaps criticos (refresh/status/governanca/metrica).
- Pendencias operacionais remanescentes:
- aplicar valores reais dos secrets no ambiente de producao e rodar checklist.
- executar e preencher validacao operacional diaria por 30 dias.

## Progresso de execucao

- WP-1: Concluida em 2026-02-20.
- WP-2: Concluida em 2026-02-20.
- WP-3: Concluida em 2026-02-20.
- WP-4: Concluida em 2026-02-20.
- WP-5: Concluida em 2026-02-20.

## Cronograma executivo

| Janela | Entrega |
|---|---|
| 2026-02-23 a 2026-02-26 | Implementacao tecnica dos 4 gaps criticos |
| 2026-02-27 | Regressao + hardening + atualizacao de docs |
| 2026-02-28 | Homologacao (staging) e smoke E2E |
| 2026-03-02 | Deploy em producao + inicio da validacao de 30 dias |
| 2026-03-02 a 2026-03-31 | Operacao monitorada e preenchimento diario |
| 2026-04-01 | Gate final Go/No-Go |

## WP-1 - Refresh token do X com rotacao automatica

### Escopo

- Implementar refresh automatico antes da expiracao.
- Evitar refresh concorrente por workspace (lock Redis).
- Persistir token novo com hash + criptografia.
- Registrar falhas com auditoria, sem vazar segredo.

### Mudancas

- Adicionar metodo de refresh no client X.
- Criar fluxo `refresh_workspace_x_tokens()` no service X.
- Ajustar `get_workspace_x_access_token()`:
- retorna token valido quando existente
- tenta refresh em expiracao/proximidade de expiracao
- retorna `None` se nao recuperar token
- Adicionar configuracoes:
- `X_AUTO_REFRESH_ENABLED` (default `true`)
- `X_REFRESH_SKEW_SECONDS` (ex.: `300`)
- `X_REFRESH_LOCK_TTL_SECONDS` (ex.: `30`)
- Adicionar metricas de sucesso/falha de refresh.

### Testes

- refresh com sucesso
- refresh com falha
- token expirado sem refresh token
- concorrencia com lock
- publicacao com token expirado recuperado via refresh

### Definition of Done

- Expiracao comum de access token nao interrompe publicacao.
- Sem segredo em log.
- Cobertura de teste dos cenarios criticos.

## WP-2 - Correcao de `oauth/status` para validade real

### Escopo

- Eliminar falso positivo de `connected=true` com token expirado.

### Mudancas

- Ajustar calculo de status no service X.
- Expandir schema com:
- `access_token_valid`
- `is_expired`
- `can_auto_refresh`
- `connected_reason`
- Atualizar endpoint de status.

### Regra recomendada

- `connected=true` somente se:
- token nao revogado
- e (`access_token_valid=true` ou `can_auto_refresh=true`)

### Testes

- token valido
- token expirado com refresh disponivel
- token expirado sem refresh
- token revogado

### Definition of Done

- endpoint de status reflete estado real de operacao.

## WP-3 - Fechar bypass de aprovacao em `/publishing/*`

### Escopo

- Garantir politica de aprovacao obrigatoria em V1.
- Permitir uso direto apenas em modo interno explicito.

### Mudancas

- Adicionar flags:
- `PUBLISHING_DIRECT_API_ENABLED=false`
- `PUBLISHING_DIRECT_API_INTERNAL_KEY`
- Restringir `/publishing/post` e `/publishing/reply`:
- apenas `owner|admin`
- bloqueio quando `PUBLISHING_DIRECT_API_ENABLED=false`
- quando habilitado, exigir header de chave interna
- Manter caminho oficial:
- fila + aprovacao via comando de controle (`/approve`).

### Testes

- member bloqueado
- owner/admin bloqueados sem flag
- owner/admin bloqueados sem chave interna quando flag ativa
- fluxo oficial de aprovacao permanece funcional

### Definition of Done

- Sem bypass de aprovacao no runtime de producao.

## WP-4 - Correcao de metrica `record_replies_published`

### Escopo

- Corrigir incremento indevido em branch bloqueado por cooldown.

### Mudancas

- Remover chamada indevida no fluxo de bloqueio.
- Garantir incremento apenas no sucesso de publicacao.

### Testes

- bloqueado por cooldown nao incrementa
- publish bem-sucedido incrementa 1 vez
- validacao no `/metrics`

### Definition of Done

- contador de replies publicadas reflete somente eventos publicados.

## WP-5 - Unificar stack e remover caminho legado

### Escopo

- Eliminar risco operacional de execucao no stack antigo.

### Mudancas

- Tornar `src.orchestrator.manager` o unico caminho canonico.
- Atualizar `systemd` e `RUNBOOK`.
- Fase transitoria:
- Sprint 1: wrappers legados com warning e delegacao
- Sprint 2: remocao definitiva de `orchestrator/` e `pipelines/` legados
- Ajustar CI para impedir reintroducao de entrypoints legados.

### Testes

- smoke do scheduler canonico
- lock/pause/kill-switch preservados
- CI verde

### Definition of Done

- Nenhum processo produtivo chama stack legado.

## Mapa de fechamento dos 4 gaps criticos

| Gap critico | Work Package | Criterio de fechamento |
|---|---|---|
| Refresh/rotacao | WP-1 | Expiracao comum nao derruba publish |
| `oauth/status` real | WP-2 | Status sem falso `connected` |
| Governanca/aprovacao | WP-3 | Sem bypass em producao |
| Metrica de published | WP-4 | Counter consistente com auditoria |

## Plano de secrets de producao

### Secrets obrigatorios

- `SECRET_KEY`
- `TOKEN_ENCRYPTION_KEY`
- `DATABASE_URL`
- `REDIS_URL`
- `X_CLIENT_ID`
- `X_CLIENT_SECRET`
- `X_REDIRECT_URI`
- `TELEGRAM_WEBHOOK_SECRET`
- `TELEGRAM_ADMINS_FILE_PATH`
- `APP_PUBLIC_BASE_URL`
- `PUBLISHING_DIRECT_API_ENABLED=false`
- `PUBLISHING_DIRECT_API_INTERNAL_KEY`

### Secrets recomendados

- `SENTRY_DSN`
- `SENTRY_TRACES_SAMPLE_RATE`

### Procedimento

1. Gerar/rotacionar credenciais fora do repositorio.
2. Configurar no ambiente de deploy.
3. Redeploy.
4. Validar `/health`, `/version`, `/metrics`.
5. Validar OAuth/status/publicacao via fluxo oficial.
6. Registrar evidencia no log operacional.

## Validacao operacional diaria (30 dias)

Periodo recomendado:
- Inicio: 2026-03-02
- Fim: 2026-03-31

Documento de evidencias:
- `docs/OPERATIONAL_VALIDATION.md`

### Rotina diaria minima

1. Executar health checks e coletar `/metrics`.
2. Confirmar status OAuth do workspace oficial.
3. Rodar ingestao e proposta de replies.
4. Aprovar fila e publicar pelo caminho oficial.
5. Revisar erros de publish e Sentry.
6. Preencher entrada diaria completa no log operacional.

### Metas de aceite

- 0 bypass de aprovacao em producao.
- 0 incidente critico de token sem recuperacao e sem alerta.
- 0 divergencia entre auditoria de publish e metrica `replies_published`.
- 0 execucao produtiva no stack legado.
- 30 entradas diarias completas com evidencia.

### Gates semanais

- Gate 1: 2026-03-08
- Gate 2: 2026-03-15
- Gate 3: 2026-03-22
- Gate final: 2026-03-31

## Go-Live final (2026-04-01)

`GO` somente se:
- 4 gaps criticos fechados e validados
- secrets de producao completos
- janela de 30 dias concluida com evidencias
- sem incidentes criticos em aberto
- sem execucao operacional em stack legado

## Entregaveis esperados por arquivo

- `src/integrations/x/x_client.py`
- `src/integrations/x/service.py`
- `src/integrations/x/router.py`
- `src/schemas/integrations_x.py`
- `src/publishing/router.py`
- `src/publishing/service.py`
- `src/core/config.py`
- `src/core/metrics.py`
- `deploy/systemd/revfirst_social.service`
- `docs/RUNBOOK.md`
- `docs/DEPLOYMENT.md`
- `tests/test_integration_x_oauth.py`
- `tests/test_publishing_engine.py`
- `tests/control/test_queue_approve.py`
