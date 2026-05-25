# PautaLimpa — Referência da API e Decisões de Arquitetura

## Fonte de Dados: API Dados Abertos da Câmara dos Deputados

| Atributo | Valor |
|---|---|
| **Base URL** | `https://dadosabertos.camara.leg.br/api/v2` |
| **Autenticação** | Nenhuma (API pública) |
| **Formato** | JSON |
| **Rate Limit** | Não documentado oficialmente; delay de 1s entre páginas recomendado |
| **Documentação** | https://dadosabertos.camara.leg.br/swagger/api.html |

### Endpoint principal utilizado

```
GET /proposicoes
```

**Parâmetros fixos do projeto:**
```
siglaTipo  = PL
ordem      = DESC
ordenarPor = id
pagina     = {1..N}
itens      = 100
```

### Estrutura da resposta (`/proposicoes`)

```json
{
  "dados": [
    {
      "id":        2340595,
      "uri":       "https://dadosabertos.camara.leg.br/api/v2/proposicoes/2340595",
      "siglaTipo": "PL",
      "codTipo":   1,
      "numero":    1234,
      "ano":       2024,
      "ementa":    "Altera a Lei nº 8.742, de 7 de dezembro de 1993..."
    }
  ],
  "links": [
    {"rel": "self",  "href": "..."},
    {"rel": "first", "href": "..."},
    {"rel": "next",  "href": "..."},
    {"rel": "last",  "href": "..."}
  ]
}
```

> **Detecção de última página:** ausência do item `{"rel": "next", ...}` no array `links`.

### Mapeamento de campos: API → Banco

| Campo API | Campo Banco | Observação |
|---|---|---|
| `id` | `id_origem` | Convertido para string |
| `siglaTipo` | `sigla_tipo` | Sempre "PL" neste pipeline |
| `numero` | `numero` | Integer |
| `ano` | `ano` | Smallint |
| `ementa` | `ementa_bruta` | Sanitizado inline |
| `uri` | `uri_camara` e `link_oficial` | URL da API |

---

## Ciclo de Vida de Status (`status_processamento_enum`)

```
           API Câmara
               │
               ▼
        [AGUARDANDO_IA]  ◄─── extractor.py insere aqui
               │
               ▼
      [EM_PROCESSAMENTO_IA]  ◄─── llm_client.py atualiza durante chamada
               │
        ┌──────┴──────┐
        │             │
        ▼             ▼
[AGUARDANDO_MIDIA] [QUARENTENA]  ◄─── falha validação (quarantine.py)
        │
        ▼
[AGUARDANDO_PUBLICACAO]  ◄─── image_generator.py
        │
        ├──── até 3 tentativas ────►  [FALHA_TRANSIENTE] → volta para fila
        │
        ▼
     [POSTADO]  ◄─── instagram_client.py
        │
        ▼
   (coleta métricas 24h depois)

Status terminais: ERRO_CAPTURA | FALHA_CRITICA | IGNORADO
```

---

## Regras de Negócio da LLM (System Prompt v1.0.0)

| # | Regra | Implementação |
|---|---|---|
| 1 | **Isenção Absoluta** | Proíbe adjetivos opinativos e inclinação política |
| 2 | **Tom Jornalístico** | Linguagem clara, sem jargão jurídico |
| 3 | **Limite de Caracteres** | Máximo 2.200 chars (limite Instagram) |
| 4 | **Citação Obrigatória** | Primeira linha: `[TIPO] - [NÚMERO]/[ANO]` |

---

## Fluxo de Retry LLM

```
Tentativa 1  →  OpenAI GPT-4o
Tentativa 2  →  OpenAI GPT-4o  (aguarda 30s após 429/5xx)
Tentativa 3  →  OpenAI GPT-4o  (aguarda 30s após 429/5xx)
     │
     ▼ (3 falhas)
Tentativa 1  →  Anthropic Claude 3 Haiku
Tentativa 2  →  Anthropic Claude 3 Haiku
Tentativa 3  →  Anthropic Claude 3 Haiku
     │
     ▼ (3 falhas)
   ERRO_LLM → QUARENTENA
```

---

## Rotação de Token Meta (Graph API)

1. Configure `META_TOKEN_EXPIRY_DATE=YYYY-MM-DD` no `.env`
2. O `Alerter.check_meta_token_expiry()` alerta 7 dias antes da expiração
3. Para renovar: acesse `developers.facebook.com` → Tools → Access Token Debugger → Extend Token
4. Atualize `META_ACCESS_TOKEN` e `META_TOKEN_EXPIRY_DATE` no `.env` de produção
5. Reinicie o scheduler

---

## Comandos de Operação

```bash
# Aplicar schema no banco
psql -U postgres -d pauta_limpa -f database/schema.sql

# Rodar extração manual
python -m ingestion.extractor

# Rodar pipeline completo manualmente
python -m pipeline.orchestrator

# Iniciar scheduler (daemon — roda continuamente)
python -m pipeline.scheduler

# Aprovar item em quarentena (Python REPL)
from governance.audit import QuarantineManager
QuarantineManager().approve(projeto_id=123, usuario="joao.revisor", observacao="Texto validado manualmente")

# Publicar nova versão do prompt
from governance.audit import PromptVersionManager
PromptVersionManager().publish_new_version(
    versao="v1.1.0",
    system_prompt="...",
    descricao="Ajuste no tom jornalístico",
    usuario="ana.po"
)
```
