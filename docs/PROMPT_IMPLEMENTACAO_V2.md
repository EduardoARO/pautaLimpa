Prompt de Implementação — PautaLimpa v2
Papel e contexto
Você é um(a) engenheiro(a) full-stack sênior especialista em Python (Flask/SQLAlchemy), PostgreSQL/Supabase, pipelines de LLM e front-end moderno (Next.js/React + Tailwind). Você vai evoluir o projeto PautaLimpa, uma plataforma que coleta proposições da Câmara dos Deputados, traduz ementas jurídicas em textos jornalísticos neutros via IA e exibe tudo numa UI web.

Arquitetura atual (NÃO quebrar contratos existentes)
Ingestão: extractor.py (LegislativeExtractor.run()) → API dadosabertos.camara.leg.br, grava em projetos_brutos.
Processamento IA: llm_client.py (LLMProcessor.run(batch_size)), primário OpenAI GPT-4o, fallback Anthropic Claude 3 Haiku; persiste em processamento_ia.
Prompt versionado: prompt_manager.py + tabela historico_prompt (apenas 1 versão ativo=TRUE). Ativação via update_prompt.py.
Schema: schema.sql (enums status_processamento_enum, status_ia_enum; tabelas projetos_brutos, processamento_ia, etc.).
Automação: auto_ingest_process.py (watcher por horários) e scheduler.py (APScheduler com cron).
Reprocessamento: reprocess_analyses.py.
UI atual: Flask + Jinja em app.py, index.html, styles.css. Deploy via gunicorn wsgi:application (Render/Procfile).
Princípios obrigatórios
Migrações aditivas e idempotentes (CREATE ... IF NOT EXISTS, ALTER TYPE ... ADD VALUE IF NOT EXISTS). Nunca dropar dados sem passo explícito e reversível.
Preservar o id_origem único e os enums existentes.
Variáveis sensíveis somente via .env (atualizar .env.example). Nunca hardcodar chaves.
Cada tarefa entrega: código + migração SQL (quando aplicável) + atualização de .env.example/README + testes (pytest).
Tarefa 1 — Análises de IA por viés ideológico (imparcial, direita, esquerda)
Objetivo: além da análise imparcial atual, gerar duas leituras interpretativas adicionais — uma sob a ótica da direita brasileira e outra sob a esquerda brasileira — explicando como cada campo político tende a interpretar/avaliar a proposição, de forma educativa e claramente rotulada como "perspectiva", não como fato.

Modelagem de dados (migração em schema.sql + script de migração):

Criar enum tipo_analise_enum com valores IMPARCIAL, DIREITA, ESQUERDA.
Refatorar para suportar múltiplas análises por projeto. Duas opções — escolha a opção B:
Opção A: adicionar colunas na processamento_ia (rejeitada: viola 1 linha por projeto).
Opção B (adotar): nova tabela analises_ia com: id, fk_projeto (FK projetos_brutos), tipo_analise tipo_analise_enum, texto_traduzido, fk_versao_prompt, status_ia status_ia_enum, prompt_tokens, completion_tokens, modelo_llm, data_processamento, data_atualizacao, UNIQUE(fk_projeto, tipo_analise). Manter processamento_ia para compat ou migrar dados existentes para analises_ia com tipo_analise='IMPARCIAL' (preferir migrar e deixar a app lendo a nova tabela).
Indexar (fk_projeto, tipo_analise) e tipo_analise.
Prompts (historico_prompt agora precisa de 3 prompts ativos por tipo):

Adicionar coluna tipo_analise tipo_analise_enum NOT NULL DEFAULT 'IMPARCIAL' em historico_prompt e trocar o índice de unicidade idx_hp_ativo_unico para único por (tipo_analise) quando ativo=TRUE.
Ajustar PromptManager para receber tipo_analise e carregar/cachear o prompt ativo correspondente.
Criar os 3 system prompts (versão v2.0.0):
IMPARCIAL: manter as 5 regras já existentes em update_prompt.py.
DIREITA e ESQUERDA: prompts que explicam a leitura típica de cada espectro no Brasil (ex.: ênfases em liberdade econômica/segurança/valores vs. proteção social/direitos/Estado), sempre com: (a) disclaimer de que é uma perspectiva analítica e não endosso; (b) proibição de ataques, desinformação ou linguagem inflamatória; (c) mesmo limite de 2.200 caracteres e citação obrigatória na 1ª linha.
Processamento (LLMProcessor):

Para cada projeto em AGUARDANDO_IA, gerar as 3 análises (loop por tipo_analise), com retry/fallback existentes, e gravar 3 linhas em analises_ia.
Status do projeto só vai para AGUARDANDO_MIDIA quando as 3 análises tiverem status_ia in (SUCESSO, FALLBACK_UTILIZADO).
Critérios de aceite: cada projeto exibe 3 abas/seções (Imparcial, Direita, Esquerda); prompts versionados e rastreáveis; testes cobrindo geração múltipla e o gating de status.

Tarefa 2 — Responsividade mobile
Objetivo: experiência mobile-first impecável (alvo principal: 360–430px).

Revisar o layout (.columns em duas colunas vira 1 coluna no mobile já em styles.css:510, mas refinar): cards com padding reduzido, tipografia fluida, toolbar de filtros colapsável/empilhada, área de toque ≥44px.
As 3 análises (Tarefa 1) devem virar abas roláveis ou acordeão no mobile, evitando paredes de texto.
Garantir meta viewport (já presente), sem scroll horizontal, imagens/logo responsivos.
Validar com Lighthouse mobile (meta: Performance ≥90, Acessibilidade ≥95) e testar em 360px, 390px, 768px, 1024px.
Critério de aceite: nenhum overflow horizontal; navegação por abas funcional no toque; checklist de breakpoints documentado.

Tarefa 3 — Remover dark mode (apenas white/light)
Remover o botão [data-theme-toggle] e o <script> de tema em index.html:23-26 e :99-111.
Remover todo o bloco :root[data-theme="dark"] e regras .theme-\* de styles.css (linhas ~21-36, 241-275).
Fixar color-scheme: light e limpar localStorage legado (pautalimpa-theme).
Garantir que a migração de FE (Tarefa 7) já nasça somente light.
Critério de aceite: nenhuma referência a dark/theme-toggle no código; UI sempre clara.

Tarefa 4 — Ingestão horária automática de novas emendas/proposições
Objetivo: alimentar o banco a cada 1 hora com novos registros vindos da Câmara.

Confirmar o endpoint correto da API de Dados Abertos para "emendas" (EP). Se o LegislativeExtractor atual só busca proposições (/proposicoes), estender para também coletar emendas (ex.: /proposicoes/{id}/emendas ou o recurso de emendas aplicável), normalizando para projetos_brutos ou criando entidade própria se o schema divergir muito. Decida e documente a abordagem; preferir reuso de projetos_brutos com sigla_tipo apropriado.
Implementar agendamento horário:
Em scheduler.py, adicionar job com CronTrigger(minute=0) (de hora em hora) que chama extração + processamento.
OU ajustar auto_ingest_process.py para intervalo horário. Padronizar em um único mecanismo para evitar duplicidade. No Render free tier (web service), avaliar usar APScheduler em background thread no processo web ou um worker dedicado; documentar a escolha em render.yaml.
Idempotência: respeitar uq_projeto_id_origem; não reprocessar duplicatas.
Adicionar variáveis: INGEST_INTERVAL_MINUTES=60, etc., em .env.example.
Critério de aceite: logs comprovam coleta a cada hora; sem duplicação; novos registros entram como AGUARDANDO_IA.

Tarefa 5 — Disparo automático das 3 análises após novos registros
Objetivo: assim que surgirem novos registros no banco, rodar automaticamente as análises imparcial, direita e esquerda.

Encadear, no mesmo ciclo horário, ingestão → processamento. Após a extração, chamar LLMProcessor().run() até esvaziar a fila AGUARDANDO_IA (reaproveitar AUTO_PROCESS_UNTIL_EMPTY).
Garantir que o processamento gere as 3 análises da Tarefa 1.
Resiliência: limitar concorrência/custo (batch configurável), respeitar rate limits, registrar tokens por tipo de análise.
Critério de aceite: novo registro capturado às HH:00 tem as 3 análises concluídas no mesmo ciclo (ou no próximo, se houver rate limit), sem intervenção manual.

Tarefa 6 — Reprocessar TODOS os registros existentes (após limpar análises antigas)
Objetivo: aprimorar as análises de todos os registros já cadastrados; antes, apagar todas as análises já feitas.

Criar script idempotente e seguro (ex.: reprocess_all.py) com flags --dry-run/--execute (espelhando o padrão de reprocess_analyses.py):
Backup: exigir confirmação e registrar contagem antes de apagar.
Purga: TRUNCATE/DELETE das análises (analises_ia e/ou processamento_ia migrada) — apenas com --execute.
Reset de status: voltar projetos_brutos.status_processamento para AGUARDANDO_IA em todos os registros elegíveis.
Reprocessar com o prompt v2.0.0 (3 análises por projeto).
Logar progresso e total de tokens; processar em lotes para não estourar quota.
Critério de aceite: após rodar, nenhum registro mantém análise antiga; todos passam a ter as 3 novas análises com fk_versao_prompt = v2.0.0.

Tarefa 7 — Migrar o front-end para a stack ideal de UI/UX
Objetivo: substituir a UI Jinja por um front moderno que melhor explore o modelo de dados do BE.

Stack recomendada: Next.js (App Router) + TypeScript + TailwindCSS + shadcn/ui + Lucide, consumindo o BE via API JSON. Somente light mode (Tarefa 3).
Camada de API: expor os dados do Flask como JSON. Adicionar endpoints em app.py (ex.: GET /api/posts com os mesmos filtros date_from, date_to, theme, limit já existentes em app.py:103-143, retornando para cada projeto as 3 análises). Manter CORS controlado.
Front: páginas com listagem agrupada por data, filtros, e card por proposição com abas Imparcial/Direita/Esquerda, badges de status, links Câmara/inteiro teor. Mobile-first.
Deploy: documentar (Vercel para o Next.js + Render para o BE, ou Next servindo tudo). Atualizar render.yaml/README conforme a escolha.
Compat: manter a UI Flask antiga funcional até o corte, ou substituir de forma atômica com instruções de rollback.
Critério de aceite: front Next.js consome a API, renderiza as 3 análises, é responsivo e light-only; build e deploy documentados.

Entregáveis finais
Migrações SQL aditivas + script de migração de dados (processamento_ia → analises_ia).
.env.example e README.md atualizados (novas vars: intervalo de ingestão, chaves de API, config do front).
Testes pytest para: geração das 3 análises, gating de status, ingestão idempotente, endpoints JSON.
Plano de execução passo a passo + comandos de rollback.
Ordem de execução sugerida
Tarefa 1 (modelo + prompts + processamento das 3 análises) — base de tudo.
Tarefa 6 (purga + reprocessamento) — popular dados no novo formato.
Tarefas 4 e 5 (ingestão horária + disparo automático).
Tarefa 7 (novo front consumindo a API) com Tarefas 2 e 3 já embutidas.
