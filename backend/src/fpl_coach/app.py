import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI

from fpl_coach.api.routes import router

load_dotenv()

# uvicorn configures its own loggers and leaves the root logger alone, so a
# plain logging.getLogger(__name__).info() anywhere in the package goes
# nowhere -- which is how the per-turn cost logging shipped inert. The reason
# it exists at all is that a question's cost was invisible, so a logger that
# silently drops the numbers is worse than none: it reads as "this question
# was cheap".
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    force=True,
)

app = FastAPI(title="FPL Coach")
app.include_router(router)

# Emitted once at import. Its real job is to prove this logger reaches the
# terminal at all: the per-turn cost logging is worthless if it is silently
# dropped, and that is exactly how it shipped the first time.
logging.getLogger(__name__).info(
    "fpl-coach up: reasoning model=%s effort=%s",
    os.environ.get("REASONING_MODEL", "claude-sonnet-5"),
    os.environ.get("REASONING_EFFORT", "medium"),
)
