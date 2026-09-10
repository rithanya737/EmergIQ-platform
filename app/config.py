"""Central config: DB URI, secret key, Ollama host, Chroma path."""
import os

from dotenv import load_dotenv

_BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(_BASE_DIR, ".env"))

SECRET_KEY = os.environ.get("SECRET_KEY", "change-me")
SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL", "mysql://user:pass@localhost/dbname")
SQLALCHEMY_TRACK_MODIFICATIONS = False
# pool_pre_ping avoids "MySQL server has gone away" (and the slow retry that
# follows it) after the connection sits idle for a while — cheap ping before
# reusing a pooled connection, recycled every hour regardless.
SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 3600}
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
CHROMA_PATH = os.environ.get("CHROMA_PATH", "./rag/chroma_db")
