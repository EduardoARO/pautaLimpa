# Deploy gratuito do PautaLimpa

Este guia prepara o deploy da interface Flask do PautaLimpa em hospedagem gratuita.

## Opção recomendada: Render Free Web Service

O projeto já inclui:

- `wsgi.py` — entrypoint WSGI
- `Procfile` — comando web para plataformas estilo Heroku
- `render.yaml` — configuração para Render
- `runtime.txt` — Python 3.12.8
- `requirements.txt` com `gunicorn`

## Antes de subir

Nunca versionar `.env`. Ele já está no `.gitignore`.

As variáveis devem ser configuradas no painel da hospedagem.

## Variáveis obrigatórias

```env
DB_HOST=db.hzzmqtkhebxeiapbesjy.supabase.co
DB_PORT=5432
DB_NAME=postgres
DB_USER=postgres
DB_PASSWORD=SUA_SENHA_SUPABASE
OPENAI_BASE_URL=https://generativelanguage.googleapis.com
OPENAI_API_KEY=SUA_CHAVE_GEMINI
OPENAI_MODEL=gemini-flash-latest
FLASK_DEBUG=false
```

## Variáveis recomendadas

```env
UI_POSTS_LIMIT=500
MAX_PAGES_PER_RUN=20
API_PAGE_SIZE=100
API_PAGE_DELAY_SECONDS=0.5
EXTRACTOR_EARLY_STOP_ON_DUPLICATE=false
LLM_BATCH_SIZE=1
AUTO_PROCESS_UNTIL_EMPTY=true
AUTO_PIPELINE_INTERVAL_SECONDS=86400
AUTO_PIPELINE_RUN_ON_START=true
```

## Deploy no Render

1. Suba o projeto para um repositório no GitHub.
2. Acesse https://render.com.
3. Clique em `New +` → `Web Service`.
4. Conecte o repositório.
5. Render deve detectar o `render.yaml` automaticamente.
6. Configure as variáveis de ambiente no painel.
7. Clique em `Deploy`.

Se configurar manualmente:

- Build Command:

```bash
pip install -r requirements.txt
```

- Start Command:

```bash
gunicorn wsgi:application
```

## Como rodar extração/IA em hospedagem gratuita

A UI web deve ficar como serviço web.

Para executar carga de dados, use uma destas opções:

### Opção A — Rodar localmente e gravar no Supabase

```bash
python backfill_dates.py
python auto_ingest_process.py
```

Como o banco é Supabase, a UI hospedada verá os dados automaticamente.

### Opção B — Render Cron Job

No Render, crie um `Cron Job` separado com comando:

```bash
python auto_ingest_process.py
```

Atenção: o plano gratuito pode ter limitações de tempo e suspensão.

## Observações importantes

- Render Free pode "dormir" após inatividade.
- Supabase Free é suficiente para testes.
- Não coloque `DB_PASSWORD` ou `OPENAI_API_KEY` no GitHub.
- O serviço web não publica no Instagram; ele apenas mostra a interface.
