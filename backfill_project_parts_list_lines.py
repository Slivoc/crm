import argparse
import csv
from collections import defaultdict

from db import db_cursor, execute as db_execute


def _normalize_part_number(value):
    if value is None:
        return ''
    if not isinstance(value, str):
        value = str(value)
    return ' '.join(value.strip().lower().split())


def _fetch_project_ids(target_project_id):
    if target_project_id is not None:
        return [target_project_id]
    rows = db_execute(
        """
        SELECT DISTINCT project_id
        FROM parts_lists
        WHERE project_id IS NOT NULL
        ORDER BY project_id
        """,
        fetch='all',
    ) or []
    return [row['project_id'] for row in rows]


def _fetch_project_parts_list_ids(project_id):
    rows = db_execute(
        """
        SELECT id
        FROM parts_lists
        WHERE project_id = ?
        ORDER BY id
        """,
        (project_id,),
        fetch='all',
    ) or []
    return [row['id'] for row in rows]


def _fetch_all(cur, query, params=None):
    cur.execute(query, params or [])
    return cur.fetchall() or []


def _import_missing_project_lines(cur, project_id, dry_run):
    missing_lines = _fetch_all(
        cur,
        """
        SELECT
            pll.id AS parts_list_line_id,
            pll.parts_list_id,
            pll.line_number,
            pll.customer_part_number,
            pll.description,
            pll.category,
            pll.customer_notes,
            pll.line_type,
            pll.quantity
        FROM parts_list_lines pll
        JOIN parts_lists pl ON pl.id = pll.parts_list_id
        LEFT JOIN project_parts_list_lines ppl
            ON ppl.parts_list_line_id = pll.id
           AND ppl.project_id = pl.project_id
        WHERE pl.project_id = ?
          AND ppl.id IS NULL
        ORDER BY pll.parts_list_id, pll.line_number, pll.id
        """,
        (project_id,),
    )

    if not missing_lines:
        return 0

    inserted = 0
    for row in missing_lines:
        if dry_run:
            inserted += 1
            continue
        cur.execute(
            """
            INSERT INTO project_parts_list_lines
                (project_id, line_number, customer_part_number, description, category, comment,
                 line_type, total_quantity, usage_by_year, status, parts_list_id, parts_list_line_id,
                 date_created, date_modified)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'linked', ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                project_id,
                row['line_number'],
                row['customer_part_number'],
                row['description'],
                row['category'],
                row['customer_notes'],
                row['line_type'] or 'normal',
                row['quantity'],
                None,
                row['parts_list_id'],
                row['parts_list_line_id'],
            ),
        )
        inserted += 1

    return inserted


def _fetch_unlinked_project_lines(cur, project_id):
    rows = _fetch_all(
        cur,
        """
        SELECT
            id,
            customer_part_number,
            parts_list_id,
            status
        FROM project_parts_list_lines
        WHERE project_id = ?
          AND parts_list_line_id IS NULL
        ORDER BY line_number, id
        """,
        (project_id,),
    )
    return rows


def _find_candidates(cur, project_id, parts_list_id, normalized_pn):
    if not normalized_pn:
        return []

    if parts_list_id:
        rows = _fetch_all(
            cur,
            """
            SELECT id, parts_list_id
            FROM parts_list_lines
            WHERE parts_list_id = ?
              AND lower(trim(customer_part_number)) = ?
            ORDER BY id
            """,
            (parts_list_id, normalized_pn),
        )
        return rows

    rows = _fetch_all(
        cur,
        """
        SELECT pll.id, pll.parts_list_id
        FROM parts_list_lines pll
        JOIN parts_lists pl ON pl.id = pll.parts_list_id
        WHERE pl.project_id = ?
          AND lower(trim(pll.customer_part_number)) = ?
        ORDER BY pll.parts_list_id, pll.id
        """,
        (project_id, normalized_pn),
    )
    return rows


def _link_project_line(cur, project_line_id, parts_list_id, parts_list_line_id, dry_run):
    if dry_run:
        return
    cur.execute(
        """
        UPDATE project_parts_list_lines
        SET parts_list_id = ?,
            parts_list_line_id = ?,
            status = CASE WHEN status = 'pending' THEN 'linked' ELSE status END,
            date_modified = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (parts_list_id, parts_list_line_id, project_line_id),
    )


def run_backfill(project_id=None, dry_run=False, import_lines=True, ambiguous_csv=None, verbose=True):
    project_ids = _fetch_project_ids(project_id)
    if not project_ids:
        if verbose:
            print("No projects with parts lists found.")
        return {
            'inserted': 0,
            'linked': 0,
            'ambiguous': 0,
            'unmatched': 0,
            'ambiguous_csv': ambiguous_csv,
        }

    ambiguous_rows = []
    unmatched_rows = []
    totals = defaultdict(int)

    with db_cursor(commit=not dry_run) as cur:
        for pid in project_ids:
            list_ids = _fetch_project_parts_list_ids(pid)
            if not list_ids:
                continue

            if import_lines:
                inserted = _import_missing_project_lines(cur, pid, dry_run)
                totals['inserted'] += inserted

            unlinked = _fetch_unlinked_project_lines(cur, pid)
            for line in unlinked:
                normalized = _normalize_part_number(line.get('customer_part_number'))
                candidates = _find_candidates(cur, pid, line.get('parts_list_id'), normalized)
                if len(candidates) == 1:
                    candidate = candidates[0]
                    _link_project_line(
                        cur,
                        line['id'],
                        candidate['parts_list_id'],
                        candidate['id'],
                        dry_run,
                    )
                    totals['linked'] += 1
                elif len(candidates) == 0:
                    unmatched_rows.append({
                        'project_id': pid,
                        'project_line_id': line['id'],
                        'customer_part_number': line.get('customer_part_number') or '',
                        'parts_list_id': line.get('parts_list_id') or '',
                    })
                    totals['unmatched'] += 1
                else:
                    ambiguous_rows.append({
                        'project_id': pid,
                        'project_line_id': line['id'],
                        'customer_part_number': line.get('customer_part_number') or '',
                        'parts_list_id': line.get('parts_list_id') or '',
                        'candidate_parts_list_ids': ','.join(str(c['parts_list_id']) for c in candidates),
                        'candidate_line_ids': ','.join(str(c['id']) for c in candidates),
                    })
                    totals['ambiguous'] += 1

    if ambiguous_csv:
        with open(ambiguous_csv, 'w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    'project_id',
                    'project_line_id',
                    'customer_part_number',
                    'parts_list_id',
                    'candidate_parts_list_ids',
                    'candidate_line_ids',
                ],
            )
            writer.writeheader()
            writer.writerows(ambiguous_rows)

    result = {
        'inserted': totals['inserted'],
        'linked': totals['linked'],
        'ambiguous': totals['ambiguous'],
        'unmatched': totals['unmatched'],
        'ambiguous_csv': ambiguous_csv if ambiguous_rows else '',
    }

    if verbose:
        if ambiguous_rows:
            print(f"Ambiguous matches: {len(ambiguous_rows)} (see {ambiguous_csv})")
        if unmatched_rows:
            print(f"Unmatched lines: {len(unmatched_rows)}")
        print(
            "Done. inserted={inserted}, linked={linked}, ambiguous={ambiguous}, unmatched={unmatched}".format(
                inserted=result['inserted'],
                linked=result['linked'],
                ambiguous=result['ambiguous'],
                unmatched=result['unmatched'],
            )
        )
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Backfill project_parts_list_lines links and optionally import parts list lines."
    )
    parser.add_argument('--project-id', type=int, default=None, help='Only process one project.')
    parser.add_argument('--dry-run', action='store_true', help='Print summary without writing changes.')
    parser.add_argument(
        '--skip-import',
        action='store_true',
        help='Skip inserting missing project_parts_list_lines from parts_list_lines.',
    )
    parser.add_argument(
        '--ambiguous-csv',
        default='backfill_project_parts_list_lines_ambiguous.csv',
        help='CSV path for ambiguous matches.',
    )
    args = parser.parse_args()

    result = run_backfill(
        project_id=args.project_id,
        dry_run=args.dry_run,
        import_lines=not args.skip_import,
        ambiguous_csv=args.ambiguous_csv,
        verbose=True,
    )
    return 0 if result else 1


if __name__ == '__main__':
    raise SystemExit(main())
