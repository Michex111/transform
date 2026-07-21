from contextlib import asynccontextmanager

import uvicorn

from fastapi import FastAPI
from src.infrastructure.database.initializer import initialize_database
from src.presentation.api.routers.conversions import router as conversion_router
from src.presentation.api.routers.upload import router as upload_router
from src.presentation.api.routers.users import router as user_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    await initialize_database()
    yield


app = FastAPI(title="File Converter API", lifespan=lifespan)

@app.get("/health")
async def health_check():
    return {"status": "ok"}


app.include_router(user_router)
app.include_router(upload_router)
app.include_router(conversion_router)


if __name__ == "__main__":
    uvicorn.run("src.presentation.api.main:app", host="0.0.0.0", port=8000, reload=True)
    