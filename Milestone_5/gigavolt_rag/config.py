"""Central configuration loaded from environment variables and an optional ``.env`` file.

Nothing secret is hard-coded here. Copy ``.env.example`` to ``.env`` and fill in values.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

M5_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = M5_ROOT.parent
OUTPUT_DIR = M5_ROOT / "output"
PROMPTS_DIR = M5_ROOT / "prompts"


def load_env_file(path: Path | None = None) -> None:
    """Load KEY=VALUE pairs into ``os.environ`` without overriding real environment variables."""
    path = path or (M5_ROOT / ".env")
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _resolve(path_value: str) -> Path:
    path = Path(path_value).expanduser()
    return path if path.is_absolute() else (M5_ROOT / path).resolve()


@dataclass(frozen=True)
class Settings:
    # MariaDB (structured ERP)
    mariadb_host: str
    mariadb_port: int
    mariadb_socket: str
    mariadb_user: str
    mariadb_password: str
    mariadb_database: str
    # MongoDB (IoT telemetry document store)
    mongo_uri: str
    mongo_db: str
    mongo_collection: str
    # ChromaDB (Milestone 4 vector store)
    chroma_path: Path
    chroma_collection: str
    embedding_model: str
    embedding_manifest: Path
    # LLM
    llm_provider: str
    anthropic_api_key: str
    anthropic_model: str
    anthropic_effort: str
    openai_api_key: str
    openai_model: str
    ollama_url: str
    ollama_model: str
    llm_max_tokens: int
    llm_timeout_s: float
    # Retrieval tuning
    vector_top_k: int
    vector_max_distance: float
    sql_row_limit: int
    mongo_event_limit: int
    high_temp_threshold_c: float
    latency_target_s: float

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> "Settings":
        load_env_file(env_file)
        provider = _env("LLM_PROVIDER", "anthropic").lower()
        return cls(
            mariadb_host=_env("MARIADB_HOST", "127.0.0.1"),
            mariadb_port=_env_int("MARIADB_PORT", 3306),
            mariadb_socket=_env("MARIADB_SOCKET", ""),
            mariadb_user=_env("MARIADB_USER", "gigavolt_rag"),
            mariadb_password=_env("MARIADB_PASSWORD", ""),
            mariadb_database=_env("MARIADB_DATABASE", "gigavolt_silver"),
            mongo_uri=_env("MONGO_URI", "mongodb://127.0.0.1:27017/?appName=gigavolt-rag"),
            mongo_db=_env("MONGO_DB", "gigavolt_iot"),
            mongo_collection=_env("MONGO_COLLECTION", "bms_telemetry"),
            chroma_path=_resolve(_env("CHROMA_PATH", "../Milestone_4/data/chroma_db")),
            chroma_collection=_env("CHROMA_COLLECTION", "gigavolt_unstructured_v1"),
            embedding_model=_env("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"),
            embedding_manifest=_resolve(
                _env("EMBEDDING_MANIFEST", "../Milestone_4/data/processed/embedding_manifest.json")
            ),
            llm_provider=provider,
            anthropic_api_key=_env("ANTHROPIC_API_KEY"),
            anthropic_model=_env("ANTHROPIC_MODEL", "claude-haiku-5-5"),
            anthropic_effort=_env("ANTHROPIC_EFFORT", "low"),
            openai_api_key=_env("OPENAI_API_KEY"),
            openai_model=_env("OPENAI_MODEL", "gpt-4o-mini"),
            ollama_url=_env("OLLAMA_URL", "http://127.0.0.1:11434"),
            ollama_model=_env("OLLAMA_MODEL", "llama3.2:3b"),
            llm_max_tokens=_env_int("LLM_MAX_TOKENS", 700),
            llm_timeout_s=_env_float("LLM_TIMEOUT_S", 20.0),
            vector_top_k=_env_int("VECTOR_TOP_K", 5),
            vector_max_distance=_env_float("VECTOR_MAX_DISTANCE", 0.80),
            sql_row_limit=_env_int("SQL_ROW_LIMIT", 10),
            mongo_event_limit=_env_int("MONGO_EVENT_LIMIT", 6),
            high_temp_threshold_c=_env_float("HIGH_TEMP_THRESHOLD_C", 45.0),
            latency_target_s=_env_float("LATENCY_TARGET_S", 5.0),
        )

    @property
    def llm_model(self) -> str:
        return {
            "anthropic": self.anthropic_model,
            "openai": self.openai_model,
            "ollama": self.ollama_model,
        }.get(self.llm_provider, "extractive (no LLM)")
