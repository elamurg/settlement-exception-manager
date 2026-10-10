import os

import psycopg
from fastapi import FastAPI, Response

app = FastAPI(title="Settlement Exception Manager")


def database_reachable() -> bool:
    url = os.environ.get("DATABASE_URL")
    if not url:
        return False
    try:
        with psycopg.connect(url, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
    except psycopg.Error:
        return False
    return True


@app.get("/health")
def health(response: Response) -> dict[str, str]:
    if not database_reachable():
        response.status_code = 503
        return {"status": "unavailable", "database": "unreachable"}
    return {"status": "ok", "database": "ok"}
