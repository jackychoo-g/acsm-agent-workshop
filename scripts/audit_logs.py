"""Print the latest analytics rows your agent wrote to BigQuery (every request, tool call and reply).

Usage: python -m scripts.audit_logs <owner>
"""

import sys

from google.cloud import bigquery

from app import config


def main() -> None:
    owner = sys.argv[1] if len(sys.argv) > 1 else config.OWNER
    table = f"{config.DATA_PROJECT}.{config.ANALYTICS_DATASET}.agent_events"
    sql = f"""
    SELECT timestamp, agent, event_type, session_id, trace_id,
           SUBSTR(TO_JSON_STRING(content), 1, 120) AS preview
    FROM `{table}`
    WHERE JSON_VALUE(attributes, '$.custom_tags.owner') = @owner
    ORDER BY timestamp DESC
    LIMIT 15
    """
    client = bigquery.Client(project=config.RUNTIME_PROJECT, location=config.REGION)
    job = client.query(
        sql,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("owner", "STRING", owner)]
        ),
    )
    rows = list(job.result())
    print(f"{len(rows)} most recent events for owner={owner} in {table}\n")
    for r in rows:
        print(f"{r.timestamp:%H:%M:%S} | {str(r.agent):<18} | {r.event_type:<22} | trace={str(r.trace_id)[:12]} | {r.preview}")


if __name__ == "__main__":
    main()
