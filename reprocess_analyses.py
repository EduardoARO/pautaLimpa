import argparse

from sqlalchemy import text

from models.database import get_session

_TARGET_STATUSES = (
    "AGUARDANDO_IA",
    "AGUARDANDO_MIDIA",
    "QUARENTENA",
)

_PREVIEW_SQL = text("""
    WITH active_prompt AS (
        SELECT id, versao
        FROM historico_prompt
        WHERE ativo = TRUE
        LIMIT 1
    ),
    candidates AS (
        SELECT
            pb.id,
            pb.sigla_tipo,
            pb.numero,
            pb.ano,
            pb.status_processamento,
            COALESCE(hp.versao, 'SEM_PROMPT') AS versao_prompt,
            length(
                trim(
                    regexp_replace(
                        COALESCE(pia.texto_traduzido, ''),
                        '^.*?(\\n|$)',
                        '',
                        's'
                    )
                )
            ) AS corpo_chars
        FROM projetos_brutos pb
        JOIN processamento_ia pia ON pia.fk_projeto = pb.id
        LEFT JOIN historico_prompt hp ON hp.id = pia.fk_versao_prompt
        CROSS JOIN active_prompt ap
        WHERE pb.status_processamento = ANY(:statuses)
          AND (
            pia.fk_versao_prompt IS DISTINCT FROM ap.id
            OR length(
                trim(
                    regexp_replace(
                        COALESCE(pia.texto_traduzido, ''),
                        '^.*?(\\n|$)',
                        '',
                        's'
                    )
                )
            ) < :min_body_chars
          )
    )
    SELECT *
    FROM candidates
    ORDER BY id DESC
""")

_RESET_SQL = text("""
    WITH active_prompt AS (
        SELECT id
        FROM historico_prompt
        WHERE ativo = TRUE
        LIMIT 1
    )
    UPDATE projetos_brutos pb
    SET status_processamento = 'AGUARDANDO_IA'
    FROM processamento_ia pia
    CROSS JOIN active_prompt ap
    WHERE pia.fk_projeto = pb.id
      AND pb.status_processamento = ANY(:statuses)
      AND (
        pia.fk_versao_prompt IS DISTINCT FROM ap.id
        OR length(
            trim(
                regexp_replace(
                    COALESCE(pia.texto_traduzido, ''),
                    '^.*?(\\n|$)',
                    '',
                    's'
                )
            )
        ) < :min_body_chars
      )
    RETURNING pb.id
""")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Recoloca na fila da IA analises feitas com prompt antigo ou corpo curto.",
    )
    parser.add_argument(
        "--min-body-chars",
        type=int,
        default=300,
        help="Tamanho minimo exigido para o corpo apos a primeira linha.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Aplica a mudanca. Sem esta flag, roda apenas em modo de visualizacao.",
    )
    parser.add_argument(
        "--limit-preview",
        type=int,
        default=15,
        help="Quantidade maxima de itens exibidos na amostra.",
    )
    return parser


def _fetch_candidates(min_body_chars: int) -> list[dict]:
    with get_session() as session:
        rows = session.execute(
            _PREVIEW_SQL,
            {
                "statuses": list(_TARGET_STATUSES),
                "min_body_chars": min_body_chars,
            },
        ).fetchall()
    return [dict(row._mapping) for row in rows]


def _reset_candidates(min_body_chars: int) -> int:
    with get_session() as session:
        rows = session.execute(
            _RESET_SQL,
            {
                "statuses": list(_TARGET_STATUSES),
                "min_body_chars": min_body_chars,
            },
        ).fetchall()
        session.commit()
    return len(rows)


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    candidates = _fetch_candidates(args.min_body_chars)

    print(f"Projetos elegiveis para reprocessamento: {len(candidates)}")
    for item in candidates[: args.limit_preview]:
        print(
            f"- id={item['id']} | {item['sigla_tipo']} {item['numero']}/{item['ano']} | "
            f"status={item['status_processamento']} | prompt={item['versao_prompt']} | "
            f"corpo_chars={item['corpo_chars']}"
        )

    if len(candidates) > args.limit_preview:
        print(f"... e mais {len(candidates) - args.limit_preview} item(ns).")

    if not args.execute:
        print("Modo de visualizacao. Use --execute para recolocar esses itens em AGUARDANDO_IA.")
        return

    updated = _reset_candidates(args.min_body_chars)
    print(f"Projetos recolocados em AGUARDANDO_IA: {updated}")


if __name__ == "__main__":
    main()
