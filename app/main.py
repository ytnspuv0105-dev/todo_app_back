from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import CORS_ORIGINS
from .database import Base, engine
from .routers import auth, invites, tasks, workspaces

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Todo Workspace API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(workspaces.router)
app.include_router(invites.router)
app.include_router(tasks.router)


@app.get("/health")
def health():
    return {"status": "ok"}
