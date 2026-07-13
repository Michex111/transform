from contextlib import asynccontextmanager

import uvicorn

from fastapi import FastAPI
from src.infrastructure.database.initializer import initialize_database


@asynccontextmanager
async def lifespan(_: FastAPI):
    await initialize_database()
    yield


app = FastAPI(lifespan=lifespan)

@app.get("/health")
async def health_check():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("src.presentation.api.main:app", host="0.0.0.0", port=8000, reload=True)
    