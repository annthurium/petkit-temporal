from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.client import shutdown
from backend.routers.feeders import router as feeders_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await shutdown()


app = FastAPI(title="PetKit Feeder Control", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(feeders_router)
