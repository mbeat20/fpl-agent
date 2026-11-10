from dotenv import load_dotenv
from fastapi import FastAPI

from fpl_coach.api.routes import router

load_dotenv()

app = FastAPI(title="FPL Coach")
app.include_router(router)
