#!/usr/bin/env bash
# Project Gigavolt | Milestone 5 | Create Milestone_5/.env (run as the normal user, NOT sudo)
#
#   bash setup/00_configure_env.sh
#
# - Copies .env.example to .env the first time (an existing .env is kept and only filled in).
# - Generates a random MARIADB_PASSWORD for the SELECT-only RAG user if none is set yet.
# - Picks a FREE LLM: a Groq free-tier key (from $GROQ_API_KEY or a hidden prompt) -> LLM_PROVIDER=groq;
#   press Enter instead -> LLM_PROVIDER=ollama (local model, no key; setup installs Ollama).
#   An $ANTHROPIC_API_KEY in the environment is also honoured (LLM_PROVIDER=anthropic).
# - chmod 600 so only you can read the secrets. .env is git-ignored.
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
M5_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="$M5_DIR/.env"

[[ $EUID -ne 0 ]] || { echo "Run as your normal user (no sudo) so .env stays owned by you." >&2; exit 1; }
[[ -f "$ENV_FILE" ]] || { cp "$M5_DIR/.env.example" "$ENV_FILE"; echo "Created $ENV_FILE from .env.example"; }
chmod 600 "$ENV_FILE"

current() { grep -E "^$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- || true; }

GROQ="${GROQ_API_KEY:-$(current GROQ_API_KEY)}"
ANTH="${ANTHROPIC_API_KEY:-$(current ANTHROPIC_API_KEY)}"
if [[ -z "$GROQ" && -z "$ANTH" && -t 0 ]]; then
  echo "Free LLM options:"
  echo "  a) Groq free tier (fast, meets the <5 s target): free key at https://console.groq.com/keys (no card)"
  echo "  b) Local Ollama model on this instance: no key at all, but slower on a CPU-only EC2"
  read -r -s -p "Paste a Groq API key for (a), or press Enter for (b): " GROQ; echo
fi

# Edit with Python (identical on GNU and BSD, safe for any characters in the values).
ENV_FILE="$ENV_FILE" NEW_GROQ="$GROQ" NEW_ANTH="$ANTH" python3 - <<'PY'
import os, re, secrets
path = os.environ["ENV_FILE"]
text = open(path, encoding="utf-8").read()

def get(name):
    m = re.search(rf"^{name}=(.*)$", text, re.M)
    return m.group(1).strip() if m else ""

def put(name, value):
    global text
    line = f"{name}={value}"
    text = re.sub(rf"^{name}=.*$", lambda _: line, text, flags=re.M) if re.search(rf"^{name}=", text, re.M) \
        else text.rstrip("\n") + "\n" + line + "\n"

notes = []
if get("MARIADB_PASSWORD") in ("", "change-me"):
    put("MARIADB_PASSWORD", "Gv5_" + secrets.token_urlsafe(18).replace("-", "x"))
    notes.append("generated MARIADB_PASSWORD")
groq, anth = os.environ.get("NEW_GROQ", "").strip(), os.environ.get("NEW_ANTH", "").strip()
if groq:
    put("GROQ_API_KEY", groq); put("LLM_PROVIDER", "groq")
    for name, value in (("VECTOR_TOP_K", "5"), ("MONGO_EVENT_LIMIT", "6"), ("SQL_ROW_LIMIT", "10"), ("LLM_MAX_TOKENS", "700")):
        put(name, value)  # full context (undo the smaller local-model settings if Ollama was used before)
    notes.append("LLM_PROVIDER=groq (free tier, " + (get("GROQ_MODEL") or "openai/gpt-oss-20b") + ")")
elif anth:
    put("ANTHROPIC_API_KEY", anth); put("LLM_PROVIDER", "anthropic")
    notes.append("LLM_PROVIDER=anthropic")
else:
    put("LLM_PROVIDER", "ollama")
    # Smaller context = faster answers from a CPU-only local model.
    for name, value in (("VECTOR_TOP_K", "3"), ("MONGO_EVENT_LIMIT", "4"), ("SQL_ROW_LIMIT", "5"), ("LLM_MAX_TOKENS", "400")):
        put(name, value)
    notes.append("no key -> LLM_PROVIDER=ollama (" + (get("OLLAMA_MODEL") or "llama3.2:3b") + ", local and free; "
                 "setup installs Ollama). Re-run this script with a Groq key any time to switch.")
open(path, "w", encoding="utf-8").write(text)
print("  " + "; ".join(notes))
PY

echo ".env ready (permissions 600). Values in use:"
grep -E "^(MARIADB_USER|MARIADB_DATABASE|MONGO_DB|CHROMA_PATH|LLM_PROVIDER|GROQ_MODEL|OLLAMA_MODEL)=" "$ENV_FILE" | sed 's/^/  /'
