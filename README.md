# PautaLimpa

# Descrição do projeto

**PautaLimpa** é uma plataforma de monitoramento legislativo que coleta automaticamente Projetos de Lei da Câmara dos Deputados, organiza as ementas por data e usa inteligência artificial para transformar textos jurídicos em resumos jornalísticos claros, neutros e acessíveis ao público geral.

O sistema foi desenvolvido para apoiar a comunicação pública sobre proposições legislativas, mantendo isenção editorial, linguagem simples e rastreabilidade das informações. Cada proposta é capturada da API oficial de Dados Abertos da Câmara, armazenada em banco PostgreSQL/Supabase e processada por IA com regras rígidas de imparcialidade, tom jornalístico, limite de caracteres e citação obrigatória do projeto.

A aplicação inclui uma interface web minimalista para visualizar as ementas originais e os textos gerados pela IA, separados por data de apresentação. Também possui scripts de automação para extração, preenchimento de datas ausentes, processamento em lote e preparação para deploy gratuito em plataformas como Render.

## Principais recursos

- **Coleta automática** de Projetos de Lei via API oficial da Câmara dos Deputados
- **Armazenamento em PostgreSQL/Supabase**
- **Processamento por IA com Gemini**
- **Geração de textos jornalísticos neutros e acessíveis**
- **Interface web minimalista para revisão editorial**
- **Agrupamento das proposições por data de apresentação**
- **Controle de status do pipeline**
- **Tratamento de erros de captura e duplicatas**
- **Preparado para deploy gratuito no Render**

## Objetivo

Tornar o acompanhamento legislativo mais compreensível para cidadãos, comunicadores e criadores de conteúdo, traduzindo ementas técnicas em explicações objetivas, sem opinião, sem viés político e em formato adequado para publicação digital.

## Estrutura do Projeto

```
PautaLimpa/
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
cp .env.example .env            # Edite com suas credenciais
python -m ingestion.extractor
```
