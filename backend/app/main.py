from fastapi import FastAPI

from app.routes import router


def create_app() -> FastAPI:
    app = FastAPI(title="VocabularyLearning", version="0.1.0")
    app.include_router(router, prefix="/api")
    return app


app = create_app()
