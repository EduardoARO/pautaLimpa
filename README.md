# PautaLimpa

Pipeline de automação para ingestão de dados legislativos, processamento via LLM e publicação no Instagram.

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
