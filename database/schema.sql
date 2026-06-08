-- =============================================================================
-- PautaLimpa — DDL Completo do Banco de Dados
-- Banco: PostgreSQL 14+
-- Cobre: Épicos 0–8 (ingestão, IA, publicação, auditoria, métricas)
-- =============================================================================

SET search_path TO public;

-- =============================================================================
-- TIPOS ENUMERADOS
-- =============================================================================

-- Ciclo de vida completo de um projeto no pipeline
CREATE TYPE status_processamento_enum AS ENUM (
    'AGUARDANDO_IA',          -- Capturado e sanitizado; aguardando fila da LLM
    'EM_PROCESSAMENTO_IA',    -- LLM está processando agora
    'AGUARDANDO_MIDIA',       -- LLM concluiu; aguardando geração de imagem
    'AGUARDANDO_PUBLICACAO',  -- Imagem gerada; aguardado janela de publicação
    'POSTADO',                -- Publicado com sucesso no Instagram
    'QUARENTENA',             -- Falha de validação; requer revisão humana
    'ERRO_CAPTURA',           -- Texto pós-sanitização < 50 chars
    'FALHA_CRITICA',          -- 3 tentativas esgotadas; intervenção manual necessária
    'IGNORADO'                -- Fora do escopo; descartado manualmente
);

-- Status do processamento pela LLM
CREATE TYPE status_ia_enum AS ENUM (
    'PENDENTE',           -- Aguardando chamada à LLM
    'PROCESSANDO',        -- Chamada em andamento
    'SUCESSO',            -- Gerado com sucesso pelo provedor principal
    'FALLBACK_UTILIZADO', -- Gerado com sucesso pelo provedor secundário
    'ERRO_LLM',           -- Falha em todos os provedores após retries
    'TOKENS_EXCEDIDOS',   -- Texto excedeu o limite mesmo após chunking
    'RECUSA_MODELO'       -- LLM recusou processar o conteúdo
);

-- Status de uma publicação no Instagram
CREATE TYPE status_publicacao_enum AS ENUM (
    'AGUARDANDO',         -- Aguardando execução
    'PUBLICADO',          -- Container criado e publicado com sucesso
    'FALHA_TRANSIENTE',   -- Erro temporário; será reenviado à fila
    'FALHA_CRITICA',      -- 3 tentativas esgotadas
    'CANCELADO'           -- Cancelado manualmente antes da publicação
);

DO $$
BEGIN
    CREATE TYPE tipo_analise_enum AS ENUM (
        'IMPARCIAL',
        'DIREITA',
        'ESQUERDA'
    );
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;


-- =============================================================================
-- TABELA 1: historico_prompt
-- Versões imutáveis dos System Prompts por tipo de análise.
-- Cada processamento referencia a versão exata ativa no momento (Épico 6).
-- =============================================================================
CREATE TABLE IF NOT EXISTS historico_prompt (
    id               BIGSERIAL PRIMARY KEY,
    versao           VARCHAR(20)  NOT NULL,        -- ex: "v1.0.0", "v1.1.0"
    descricao        TEXT,                         -- changelog da versão
    tipo_analise     tipo_analise_enum NOT NULL DEFAULT 'IMPARCIAL',
    system_prompt    TEXT         NOT NULL,        -- conteúdo completo do prompt
    ativo            BOOLEAN      NOT NULL DEFAULT TRUE,
    criado_em        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_prompt_versao UNIQUE (versao, tipo_analise)
);

COMMENT ON TABLE historico_prompt IS 'Controle de versão imutável dos System Prompts da LLM por tipo de análise (auditoria Épico 6).';

ALTER TABLE IF EXISTS historico_prompt
    ADD COLUMN IF NOT EXISTS tipo_analise tipo_analise_enum NOT NULL DEFAULT 'IMPARCIAL';

UPDATE historico_prompt
SET tipo_analise = COALESCE(tipo_analise, 'IMPARCIAL');

ALTER TABLE IF EXISTS historico_prompt
    DROP CONSTRAINT IF EXISTS uq_prompt_versao;

ALTER TABLE IF EXISTS historico_prompt
    DROP CONSTRAINT IF EXISTS uq_prompt_versao_tipo;

ALTER TABLE IF EXISTS historico_prompt
    ADD CONSTRAINT uq_prompt_versao_tipo UNIQUE (versao, tipo_analise);

-- Apenas uma versão pode estar ativa por vez
DROP INDEX IF EXISTS idx_hp_ativo_unico;

CREATE UNIQUE INDEX IF NOT EXISTS idx_hp_ativo_tipo_unico
    ON historico_prompt (tipo_analise) WHERE ativo = TRUE;

CREATE INDEX IF NOT EXISTS idx_hp_tipo_analise
    ON historico_prompt (tipo_analise);


-- =============================================================================
-- TABELA 2: projetos_brutos
-- Dados brutos da API da Câmara dos Deputados, sem transformação.
-- Chave de unicidade: (id_origem) — o ID da Câmara já é único globalmente.
-- =============================================================================
CREATE TABLE IF NOT EXISTS projetos_brutos (
    id                    BIGSERIAL PRIMARY KEY,

    -- ID único conforme retornado pela API da Câmara (ex: 2340595)
    id_origem             VARCHAR(100)              NOT NULL,

    -- Sigla do tipo da proposição (ex: "PL", "PLP", "PDL")
    sigla_tipo            VARCHAR(10)               NOT NULL DEFAULT 'PL',

    -- Número sequencial do projeto (ex: 1234)
    numero                INTEGER,

    -- Ano de apresentação do projeto
    ano                   SMALLINT                  NOT NULL,

    -- Texto bruto da ementa conforme retornado pela API (pode conter HTML)
    ementa_bruta          TEXT                      NOT NULL,

    -- URI da API da Câmara para busca de detalhes completos
    uri_camara            VARCHAR(2048),

    -- URL da página oficial do projeto no portal da Câmara
    link_oficial          VARCHAR(2048),

    -- URL do inteiro teor (PDF/HTML do texto completo)
    url_inteiro_teor      VARCHAR(2048),

    -- Data de apresentação do projeto na Câmara
    data_apresentacao     DATE,

    -- Estado atual do projeto no pipeline interno
    status_processamento  status_processamento_enum NOT NULL DEFAULT 'AGUARDANDO_IA',

    -- Contador de tentativas de republicação (Épico 7 — retroalimentação)
    tentativas_publicacao SMALLINT                  NOT NULL DEFAULT 0,

    -- Timestamp de captura e última atualização
    data_captura          TIMESTAMPTZ               NOT NULL DEFAULT NOW(),
    data_atualizacao      TIMESTAMPTZ               NOT NULL DEFAULT NOW(),

    -- Unicidade: ID da Câmara é globalmente único
    CONSTRAINT uq_projeto_id_origem UNIQUE (id_origem)
);

COMMENT ON TABLE  projetos_brutos IS 'Dados brutos capturados da API dadosabertos.camara.leg.br, sem transformação.';
COMMENT ON COLUMN projetos_brutos.id_origem IS 'ID numérico do projeto conforme API da Câmara dos Deputados.';
COMMENT ON COLUMN projetos_brutos.tentativas_publicacao IS 'Contador de tentativas de publicação no Instagram para controle de retroalimentação.';

CREATE INDEX IF NOT EXISTS idx_pb_id_origem          ON projetos_brutos (id_origem);
CREATE INDEX IF NOT EXISTS idx_pb_status             ON projetos_brutos (status_processamento);
CREATE INDEX IF NOT EXISTS idx_pb_ano                ON projetos_brutos (ano);
CREATE INDEX IF NOT EXISTS idx_pb_ano_status         ON projetos_brutos (ano, status_processamento);
CREATE INDEX IF NOT EXISTS idx_pb_data_captura       ON projetos_brutos (data_captura DESC);


-- =============================================================================
-- TABELA 3: processamento_ia
-- Resultado do processamento LLM de cada projeto.
-- Relação: 1 projeto_bruto → 0..1 processamento_ia
-- =============================================================================
CREATE TABLE IF NOT EXISTS processamento_ia (
    id                    BIGSERIAL PRIMARY KEY,

    fk_projeto            BIGINT         NOT NULL
        REFERENCES projetos_brutos (id) ON DELETE CASCADE,

    -- FK para a versão do System Prompt usada (rastreabilidade Épico 6)
    fk_versao_prompt      BIGINT
        REFERENCES historico_prompt (id) ON DELETE SET NULL,

    -- Texto após sanitização (HTML removido, UTF-8 normalizado)
    texto_limpo           TEXT,

    -- Saída da LLM: resumo em linguagem popular para publicação
    texto_traduzido       TEXT,

    -- Status do processamento LLM
    status_ia             status_ia_enum NOT NULL DEFAULT 'PENDENTE',

    -- Tokens consumidos separados para controle de custo (Épico 5 — FinOps)
    prompt_tokens         INTEGER        CHECK (prompt_tokens >= 0),
    completion_tokens     INTEGER        CHECK (completion_tokens >= 0),

    -- Modelo efetivamente utilizado (principal ou fallback)
    modelo_llm            VARCHAR(100),

    -- Indica se o texto foi truncado por exceder o limite de tokens
    processado_parcialmente BOOLEAN      NOT NULL DEFAULT FALSE,

    data_processamento    TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    data_atualizacao      TIMESTAMPTZ    NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_processamento_projeto UNIQUE (fk_projeto)
);

COMMENT ON TABLE  processamento_ia IS 'Resultado do processamento via LLM; referencia a versão do prompt usada para auditoria.';
COMMENT ON COLUMN processamento_ia.processado_parcialmente IS 'TRUE se o texto foi truncado por exceder o context window da LLM.';

CREATE INDEX IF NOT EXISTS idx_pia_fk_projeto    ON processamento_ia (fk_projeto);
CREATE INDEX IF NOT EXISTS idx_pia_status_ia     ON processamento_ia (status_ia);
CREATE INDEX IF NOT EXISTS idx_pia_versao_prompt ON processamento_ia (fk_versao_prompt);


-- =============================================================================
-- TABELA 3B: analises_ia
-- Resultado do processamento LLM por projeto e por tipo de análise.
-- Relação: 1 projeto_bruto → 0..3 analises_ia
-- =============================================================================
CREATE TABLE IF NOT EXISTS analises_ia (
    id                    BIGSERIAL PRIMARY KEY,

    fk_projeto            BIGINT         NOT NULL
        REFERENCES projetos_brutos (id) ON DELETE CASCADE,

    tipo_analise          tipo_analise_enum NOT NULL DEFAULT 'IMPARCIAL',

    -- FK para a versão do System Prompt usada (rastreabilidade Épico 6)
    fk_versao_prompt      BIGINT
        REFERENCES historico_prompt (id) ON DELETE SET NULL,

    -- Texto após sanitização (HTML removido, UTF-8 normalizado)
    texto_limpo           TEXT,

    -- Saída da LLM: texto para cada perspectiva/análise
    texto_traduzido       TEXT,

    -- Status do processamento LLM
    status_ia             status_ia_enum NOT NULL DEFAULT 'PENDENTE',

    -- Tokens consumidos separados para controle de custo (Épico 5 — FinOps)
    prompt_tokens         INTEGER        CHECK (prompt_tokens >= 0),
    completion_tokens     INTEGER        CHECK (completion_tokens >= 0),

    -- Modelo efetivamente utilizado (principal ou fallback)
    modelo_llm            VARCHAR(100),

    -- Indica se o texto foi truncado por exceder o limite de tokens
    processado_parcialmente BOOLEAN      NOT NULL DEFAULT FALSE,

    data_processamento    TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    data_atualizacao      TIMESTAMPTZ    NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_analises_projeto_tipo UNIQUE (fk_projeto, tipo_analise)
);

COMMENT ON TABLE analises_ia IS 'Resultado do processamento via LLM separado por tipo de análise; referencia a versão do prompt usada para auditoria.';
COMMENT ON COLUMN analises_ia.tipo_analise IS 'Classificação da leitura gerada pela IA: IMPARCIAL, DIREITA ou ESQUERDA.';
COMMENT ON COLUMN analises_ia.processado_parcialmente IS 'TRUE se o texto foi truncado por exceder o context window da LLM.';

CREATE INDEX IF NOT EXISTS idx_ai_fk_projeto    ON analises_ia (fk_projeto);
CREATE INDEX IF NOT EXISTS idx_ai_tipo_analise  ON analises_ia (tipo_analise);
CREATE INDEX IF NOT EXISTS idx_ai_status_ia     ON analises_ia (status_ia);
CREATE INDEX IF NOT EXISTS idx_ai_versao_prompt ON analises_ia (fk_versao_prompt);


-- =============================================================================
-- TABELA 4: publicacoes_instagram
-- Registro de cada publicação no Instagram (container + mídia).
-- Relação: 1 projeto_bruto → 0..1 publicacao_instagram
-- =============================================================================
CREATE TABLE IF NOT EXISTS publicacoes_instagram (
    id                    BIGSERIAL PRIMARY KEY,

    fk_projeto            BIGINT              NOT NULL
        REFERENCES projetos_brutos (id) ON DELETE CASCADE,

    -- IDs retornados pela Graph API da Meta
    container_id          VARCHAR(100),           -- ID do container de mídia criado
    media_id              VARCHAR(100),           -- ID final da mídia publicada

    -- Conteúdo da publicação
    caption_usada         TEXT,
    url_imagem            VARCHAR(2048),          -- URL pública temporária da imagem
    template_utilizado    VARCHAR(100),           -- Flag para A/B testing (Épico 8)

    status                status_publicacao_enum  NOT NULL DEFAULT 'AGUARDANDO',

    -- Data em que a imagem temporária pode ser deletada (preenchida pelo app: publicacao + 24h)
    data_publicacao       TIMESTAMPTZ,
    data_delecao_imagem   TIMESTAMPTZ,

    data_atualizacao      TIMESTAMPTZ            NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_publicacao_projeto UNIQUE (fk_projeto)
);

COMMENT ON TABLE  publicacoes_instagram IS 'Registro de publicações no Instagram via Graph API; rastreia Media ID para métricas.';
COMMENT ON COLUMN publicacoes_instagram.media_id IS 'Instagram Media ID; necessário para consultar /insights (Épico 7).';
COMMENT ON COLUMN publicacoes_instagram.data_delecao_imagem IS 'Calculado automaticamente: 24h após publicação, imagem temporária deve ser deletada.';

CREATE INDEX IF NOT EXISTS idx_pi_fk_projeto     ON publicacoes_instagram (fk_projeto);
CREATE INDEX IF NOT EXISTS idx_pi_status         ON publicacoes_instagram (status);
CREATE INDEX IF NOT EXISTS idx_pi_media_id       ON publicacoes_instagram (media_id);
CREATE INDEX IF NOT EXISTS idx_pi_delecao_imagem ON publicacoes_instagram (data_delecao_imagem)
    WHERE data_publicacao IS NOT NULL;


-- =============================================================================
-- TABELA 5: logs_publicacao
-- Log imutável de cada tentativa de publicação (sucessos e falhas).
-- Permite rastrear histórico completo de retentativas.
-- =============================================================================
CREATE TABLE IF NOT EXISTS logs_publicacao (
    id                BIGSERIAL PRIMARY KEY,
    fk_projeto        BIGINT        NOT NULL
        REFERENCES projetos_brutos (id) ON DELETE CASCADE,
    tentativa         SMALLINT      NOT NULL DEFAULT 1,
    status            VARCHAR(50)   NOT NULL,     -- código de status HTTP ou descrição
    mensagem          TEXT,                       -- detalhe do erro ou sucesso
    payload_enviado   JSONB,                      -- snapshot do payload para debug
    resposta_api      JSONB,                      -- resposta raw da Graph API
    registrado_em     TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE logs_publicacao IS 'Log imutável de tentativas de publicação; nunca atualizar, apenas inserir.';

CREATE INDEX IF NOT EXISTS idx_lp_fk_projeto  ON logs_publicacao (fk_projeto);
CREATE INDEX IF NOT EXISTS idx_lp_registrado  ON logs_publicacao (registrado_em DESC);


-- =============================================================================
-- TABELA 6: metricas_engajamento
-- Métricas coletadas via Graph API /insights 24h após publicação (Épico 7).
-- =============================================================================
CREATE TABLE IF NOT EXISTS metricas_engajamento (
    id                  BIGSERIAL PRIMARY KEY,
    fk_publicacao       BIGINT    NOT NULL
        REFERENCES publicacoes_instagram (id) ON DELETE CASCADE,
    curtidas            INTEGER   NOT NULL DEFAULT 0,
    comentarios         INTEGER   NOT NULL DEFAULT 0,
    compartilhamentos   INTEGER   NOT NULL DEFAULT 0,
    salvamentos         INTEGER   NOT NULL DEFAULT 0,
    alcance             INTEGER   NOT NULL DEFAULT 0,
    impressoes          INTEGER   NOT NULL DEFAULT 0,
    data_consulta       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE metricas_engajamento IS 'Métricas de engajamento coletadas via /insights da Graph API 24h após cada post.';

CREATE INDEX IF NOT EXISTS idx_me_fk_publicacao ON metricas_engajamento (fk_publicacao);
CREATE INDEX IF NOT EXISTS idx_me_data_consulta ON metricas_engajamento (data_consulta DESC);


-- =============================================================================
-- TABELA 7: auditoria
-- Trilha de auditoria imutável para intervenções manuais (Épico 6).
-- Registra toda ação humana no sistema (aprovação de quarentena, etc.).
-- =============================================================================
CREATE TABLE IF NOT EXISTS auditoria (
    id            BIGSERIAL PRIMARY KEY,
    entidade      VARCHAR(100)  NOT NULL,  -- nome da tabela afetada
    id_entidade   BIGINT        NOT NULL,  -- PK do registro afetado
    acao          VARCHAR(100)  NOT NULL,  -- ex: APPROVE_QUARANTINE, FORCE_REPROCESS
    usuario       VARCHAR(100)  NOT NULL,  -- usuário ou processo responsável
    dados_antes   JSONB,                   -- snapshot do estado anterior
    dados_depois  JSONB,                   -- snapshot do estado posterior
    observacao    TEXT,
    registrado_em TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE auditoria IS 'Trilha imutável de auditoria para toda intervenção manual (Épico 6 — compliance).';

CREATE INDEX IF NOT EXISTS idx_aud_entidade    ON auditoria (entidade, id_entidade);
CREATE INDEX IF NOT EXISTS idx_aud_registrado  ON auditoria (registrado_em DESC);


-- =============================================================================
-- FUNÇÃO + TRIGGERS: atualiza data_atualizacao automaticamente em qualquer UPDATE
-- =============================================================================
CREATE OR REPLACE FUNCTION fn_set_data_atualizacao()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.data_atualizacao = NOW();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_pb_data_atualizacao
    BEFORE UPDATE ON projetos_brutos
    FOR EACH ROW EXECUTE FUNCTION fn_set_data_atualizacao();

CREATE TRIGGER trg_pia_data_atualizacao
    BEFORE UPDATE ON processamento_ia
    FOR EACH ROW EXECUTE FUNCTION fn_set_data_atualizacao();

CREATE TRIGGER trg_pi_data_atualizacao
    BEFORE UPDATE ON publicacoes_instagram
    FOR EACH ROW EXECUTE FUNCTION fn_set_data_atualizacao();


-- =============================================================================
-- SEED: Versão inicial do System Prompt (4 Regras de Negócio)
-- =============================================================================
INSERT INTO historico_prompt (versao, descricao, system_prompt, ativo)
VALUES (
    'v1.0.0',
    'Versão inicial das 4 Regras de Negócio do PautaLimpa.',
    'Você é um jornalista legislativo especializado em traduzir textos jurídicos complexos para linguagem acessível ao cidadão comum. Siga ESTRITAMENTE as 4 regras abaixo:

REGRA 1 — ISENÇÃO ABSOLUTA: Seja estritamente analítico e imparcial. É proibido usar adjetivos opinativos, julgamentos de valor ou qualquer inclinação política (ex: "excelente projeto", "medida irresponsável"). Descreva apenas o que a lei faz.

REGRA 2 — TOM JORNALÍSTICO: Use linguagem clara, objetiva e acessível. Evite jargões jurídicos sem explicação. Escreva como uma notícia de jornal popular, não como um parecer jurídico.

REGRA 3 — LIMITE DE CARACTERES: Sua resposta NUNCA pode ultrapassar 2.200 caracteres (limite do Instagram). Seja conciso. Se necessário, priorize: impacto direto na vida do cidadão > contexto > detalhes técnicos.

REGRA 4 — CITAÇÃO OBRIGATÓRIA: A PRIMEIRA LINHA da sua resposta DEVE conter obrigatoriamente o identificador no formato exato: [TIPO DA LEI] - [NÚMERO]/[ANO]
Exemplo: PL - 1234/2024

Após a citação, siga com o resumo em linguagem popular.',
    TRUE
)
ON CONFLICT (versao) DO NOTHING;
