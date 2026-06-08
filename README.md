# PautaLimpa

# Descrição do projeto

**PautaLimpa** é uma plataforma de monitoramento legislativo que coleta automaticamente Projetos de Lei da Câmara dos Deputados, organiza as ementas por data e usa inteligência artificial para transformar textos jurídicos em três leituras jornalísticas distintas — imparcial, direita e esquerda — sempre com linguagem clara, neutra e acessível ao público geral.

O sistema foi desenvolvido para apoiar a comunicação pública sobre proposições legislativas, mantendo rastreabilidade das informações e separação explícita entre perspectiva analítica e fato. Cada proposta é capturada da API oficial de Dados Abertos da Câmara, armazenada em banco PostgreSQL/Supabase e processada por IA com regras rígidas de citação obrigatória, limite de caracteres e geração de múltiplas leituras por tipo de análise.

A aplicação inclui uma interface web minimalista para visualizar as ementas originais e os textos gerados pela IA, separados por data de apresentação. A UI agora mostra as três análises por proposição em acordeões, com melhor experiência mobile. Também possui scripts de automação para extração, preenchimento de datas ausentes, processamento em lote e um ciclo horário compartilhado para ingestão + IA, pronto para deploy gratuito em plataformas como Render.

## Principais recursos

- **Coleta automática** de Projetos de Lei via API oficial da Câmara dos Deputados
- **Armazenamento em PostgreSQL/Supabase**
- **Processamento por IA com Gemini/OpenAI + fallback Anthropic**
- **Geração de três leituras por proposição: imparcial, direita e esquerda**
- **Interface web minimalista para revisão editorial**
- **Agrupamento das proposições por data de apresentação**
- **Controle de status do pipeline**
- **Tratamento de erros de captura e duplicatas**
- **Script de migração/backfill para levar análises legadas para o novo formato**
- **Preparado para deploy gratuito no Render**

## Objetivo

Tornar o acompanhamento legislativo mais compreensível para cidadãos, comunicadores e criadores de conteúdo, traduzindo ementas técnicas em explicações objetivas, sem opinião, sem viés político e em formato adequado para publicação digital.

## Estrutura do Projeto

```
PautaLimpa/
├── backend/                    # API Flask oficial + compatibilidade WSGI
│   ├── __init__.py
│   ├── app.py                  # Ponto de entrada da API Flask
│   ├── rag_search.py           # Compat layer para busca semântica
│   └── wsgi.py                 # WSGI do backend
├── frontend/                   # Frontend Next.js
│   ├── app/
│   ├── components/
│   ├── package.json
│   └── next.config.mjs
├── ingestion/                  # Scripts de captação e limpeza de dados
│   ├── __init__.py
│   └── extractor.py            # Script principal de ingestão (Épico 1)
├── models/                     # Modelos ORM e definições de banco de dados
│   ├── __init__.py
│   └── database.py             # Engine SQLAlchemy e Session
├── processing/                 # Processamento de texto via LLM (Épico 2)
│   └── __init__.py
├── publishing/                 # Publicação no Instagram (Épico 3)
│   └── __init__.py
├── utils/                      # Utilitários compartilhados
│   ├── __init__.py
│   └── logger.py               # Configuração centralizada de logs
├── database/                   # Scripts DDL e migrações
│   └── schema.sql              # Definição das tabelas PostgreSQL
├── logs/                       # Arquivos de log gerados em runtime (gitignored)
├── web/                        # Camada legada/compatibilidade do backend enquanto a migração estabiliza
├── .env.example                # Modelo de variáveis de ambiente
├── .gitignore
├── requirements.txt
└── README.md
```

## Como Executar

Veja as instruções em cada artefato. Resumo rápido:

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
cd frontend && npm install
cp .env.example .env            # Edite com suas credenciais
python -m ingestion.extractor
```

## Rodar o projeto completo localmente

Com um único comando, suba a UI Flask e o scheduler horário juntos:

```bash
py -3 run_local.py
```

Ao iniciar, o scheduler executa **uma ingestão + processamento de IA imediatamente** e depois segue o cron horário normal. Isso ajuda a popular o banco logo na primeira subida.

Se você quiser **recomeçar do zero** no banco local, use:

```bash
py -3 reset_local_db.py --confirm
```

Esse comando limpa os registros do banco local, recria os prompts aprovados e deixa o projeto pronto para subir novamente.

## Novos comandos de script

```bash
# Backend Flask API isolado
py -3 -m backend.app

# Frontend Next isolado
npm --prefix frontend run dev

# Scheduler isolado (ingestão horária + publicação + métricas)
py -3 -m pipeline.scheduler

# Ciclo horário compartilhado manualmente
py -3 auto_ingest_process.py

# Seed dos prompts v2.0.0
py -3 update_prompt.py

# Migração/backfill da análise imparcial legada
py -3 migrate_v2.py --dry-run
py -3 migrate_v2.py --execute
```

> Observação: o carrossel do frontend usa `Embla` para drag nativo React/Next, e o RAG combina ranking semântico + lexical para ficar mais resiliente.

> Observação: `web/` ficou como camada de compatibilidade enquanto a migração para `backend/` e `frontend/` estabiliza.
