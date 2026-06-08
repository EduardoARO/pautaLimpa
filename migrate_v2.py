"""migra dados legados de processamento_ia para analises_ia.

Uso:
    python migrate_v2.py            # mostra o que seria migrado
    python migrate_v2.py --execute   # aplica a migração
"""

from __future__ import annotations

import argparse

from sqlalchemy import text

from models.database import get_session

_PREVIEW_SQL = text(
    """
    SELECT
        pb.id,
        pb.sigla_tipo,
        pb.numero,
        pb.ano,
        pia.status_ia,
        pia.fk_versao_prompt,
        COALESCE(ai.id, 0) AS analise_imparcial_existente
    FROM projetos_brutos pb
    JOIN processamento_ia pia ON pia.fk_projeto = pb.id
    LEFT JOIN analises_ia ai
        ON ai.fk_projeto = pb.id
       AND ai.tipo_analise = 'IMPARCIAL'
    WHERE pia.status_ia IN ('SUCESSO', 'FALLBACK_UTILIZADO', 'RECUSA_MODELO', 'ERRO_LLM')
    ORDER BY pb.id DESC
    """
)

_MIGRATE_SQL = text(
    """
    INSERT INTO analises_ia (
        fk_projeto,
        tipo_analise,
        fk_versao_prompt,
        texto_limpo,
        texto_traduzido,
        status_ia,
        prompt_tokens,
        completion_tokens,
        modelo_llm,
        processado_parcialmente,
        data_processamento,
        data_atualizacao
    )
    SELECT
        pia.fk_projeto,
        'IMPARCIAL'::tipo_analise_enum,
        pia.fk_versao_prompt,
        pia.texto_limpo,
        pia.texto_traduzido,
        pia.status_ia,
        pia.prompt_tokens,
        pia.completion_tokens,
        pia.modelo_llm,
        pia.processado_parcialmente,
        pia.data_processamento,
        pia.data_atualizacao
    FROM processamento_ia pia
    WHERE pia.status_ia IN ('SUCESSO', 'FALLBACK_UTILIZADO', 'RECUSA_MODELO', 'ERRO_LLM')
      AND NOT EXISTS (
          SELECT 1
          FROM analises_ia ai
          WHERE ai.fk_projeto = pia.fk_projeto
            AND ai.tipo_analise = 'IMPARCIAL'
      )
    """
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Migra analises legadas para a nova tabela analises_ia.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostra o preview sem aplicar nenhuma mudanca.",
    )
    group.add_argument(
        "--execute",
        action="store_true",
        help="Executa a migracao. Sem esta flag, o comportamento padrao e dry-run.",
    )
    parser.add_argument(
        "--limit-preview",
        type=int,
        default=20,
        help="Quantidade maxima de linhas exibidas no preview.",
    )
    return parser


def _preview() -> list[dict]:
    with get_session() as session:
        rows = session.execute(_PREVIEW_SQL).fetchall()
    return [dict(row._mapping) for row in rows]


def _migrate() -> int:
    with get_session() as session:
        result = session.execute(_MIGRATE_SQL)
        session.commit()
        return result.rowcount or 0


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    preview = _preview()
    print(f"Itens elegiveis para migracao: {len(preview)}")
    for item in preview[: args.limit_preview]:
        print(
            f"- id={item['id']} | {item['sigla_tipo']} {item['numero']}/{item['ano']} | "
            f"status={item['status_ia']} | imparcial_existente={bool(item['analise_imparcial_existente'])}"
        )

    if len(preview) > args.limit_preview:
        print(f"... e mais {len(preview) - args.limit_preview} item(ns).")

    if args.dry_run or not args.execute:
        print("Modo de visualizacao. Use --execute para copiar os registros para analises_ia.")
        return

    migrated = _migrate()
    print(f"Registros migrados para analises_ia: {migrated}")


if __name__ == "__main__":
    main()
