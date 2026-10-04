from fastapi import FastAPI

app = FastAPI(title="Settlement Exception Manager")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
