"""PostgreSQL query for the rights calendar."""

from sqlalchemy import text


ANALYZE_SCREENINGS = text("""
WITH screenings AS (
    SELECT * FROM jsonb_to_recordset(CAST(:screenings AS jsonb)) AS s(
        screening_id text, film_id text, title text, territory text, medium text,
        starts_at timestamptz, ends_at timestamptz
    )
), rights AS (
    SELECT * FROM jsonb_to_recordset(CAST(:rights AS jsonb)) AS r(
        right_id text, film_id text, territory text, medium text,
        starts_at timestamptz, ends_at timestamptz
    )
), prior_coverage AS (
    -- An earlier, longer grant can still reach past the immediately preceding grant.
    SELECT s.screening_id, r.right_id, r.starts_at, r.ends_at,
           MAX(r.ends_at) OVER (
               PARTITION BY s.screening_id
               ORDER BY r.starts_at, r.ends_at, r.right_id
               ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
           ) AS prior_end
    FROM screenings s
    JOIN rights r ON r.film_id = s.film_id
                 AND r.territory IN (s.territory, '*')
                 AND r.medium IN (s.medium, '*')
                 AND r.ends_at > s.starts_at
), island_members AS (
    SELECT screening_id, starts_at, ends_at,
           SUM(CASE WHEN prior_end IS NULL OR starts_at > prior_end THEN 1 ELSE 0 END)
               OVER (PARTITION BY screening_id ORDER BY starts_at, ends_at, right_id
                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS island_id
    FROM prior_coverage
), rights_islands AS (
    SELECT screening_id, MIN(starts_at) AS starts_at, MAX(ends_at) AS ends_at
    FROM island_members
    GROUP BY screening_id, island_id
), coverage_with_boundaries AS (
    SELECT s.screening_id, GREATEST(s.starts_at, i.starts_at) AS starts_at,
           LEAST(s.ends_at, i.ends_at) AS ends_at
    FROM screenings s
    JOIN rights_islands i ON i.screening_id = s.screening_id
                         AND i.starts_at < s.ends_at AND i.ends_at > s.starts_at
    UNION ALL
    SELECT screening_id, starts_at, starts_at FROM screenings
    UNION ALL
    SELECT screening_id, ends_at, ends_at FROM screenings
), gap_candidates AS (
    -- Zero-width boundaries let the same LEAD find head, interior and tail gaps.
    SELECT screening_id, ends_at AS gap_start,
           LEAD(starts_at) OVER (
               PARTITION BY screening_id ORDER BY starts_at, ends_at
           ) AS gap_end
    FROM coverage_with_boundaries
), gaps AS (
    SELECT screening_id, gap_start, gap_end,
           CAST(EXTRACT(EPOCH FROM (gap_end - gap_start)) / 60 AS bigint) AS gap_minutes
    FROM gap_candidates
    WHERE gap_start < gap_end
), delay_candidates AS (
    SELECT s.screening_id, s.starts_at AS original_start, s.ends_at - s.starts_at AS duration,
           GREATEST(s.starts_at, i.starts_at) AS proposed_start, i.ends_at AS coverage_end
    FROM screenings s
    JOIN rights_islands i ON i.screening_id = s.screening_id
), proposals AS (
    SELECT screening_id, MIN(proposed_start) AS proposed_start
    FROM delay_candidates
    WHERE coverage_end - proposed_start >= duration
      AND EXTRACT(EPOCH FROM (proposed_start - original_start))
          <= CAST(:max_delay_minutes AS numeric) * 60
    GROUP BY screening_id
)
SELECT s.screening_id, s.film_id, s.title, s.territory, s.medium, s.starts_at, s.ends_at,
       CAST(EXTRACT(EPOCH FROM (s.ends_at - s.starts_at)) / 60 AS bigint) AS screening_minutes,
       g.gap_start, g.gap_end, g.gap_minutes,
       CAST(SUM(COALESCE(g.gap_minutes, 0)) OVER (PARTITION BY s.screening_id) AS bigint)
           AS unauthorized_minutes,
       p.proposed_start, p.proposed_start + (s.ends_at - s.starts_at) AS proposed_end
FROM screenings s
LEFT JOIN gaps g ON g.screening_id = s.screening_id
LEFT JOIN proposals p ON p.screening_id = s.screening_id
ORDER BY s.starts_at, s.screening_id, g.gap_start
""")
