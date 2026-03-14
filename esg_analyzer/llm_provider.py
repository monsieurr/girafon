"""
llm_provider.py
---------------
Thin, unified LLM interface using litellm.
Supports any provider: Anthropic, OpenAI, Ollama, Mistral, Groq, etc.

User configures via environment variables:
  LLM_PROVIDER=anthropic   MODEL=claude-sonnet-4-20250514
  LLM_PROVIDER=ollama      MODEL=llama3.2
  LLM_PROVIDER=openai      MODEL=gpt-4o

No other file in this codebase imports litellm directly.

Two call surfaces are exposed:
  call_llm()       — synchronous (used by --check and one-off calls)
  call_llm_async() — async (used by detect_all for concurrent analysis)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

try:
    import litellm
    litellm.suppress_debug_info = True
    litellm.verbose = False
    litellm.set_verbose = False
    # LiteLLM uses several logger names depending on version — suppress all of them
    for _log_name in ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy", "litellm", "litellm.utils", "litellm.main"):
        logging.getLogger(_log_name).setLevel(logging.WARNING)
except ImportError:
    raise ImportError(
        "Install litellm: pip install litellm\n"
        "It supports Anthropic, OpenAI, Ollama, Mistral, Groq, and 100+ others."
    )

logger = logging.getLogger(__name__)

# ── Provider registry ──────────────────────────────────────────────────────────

_PROVIDER_DOCS = {
    "gemini":    "Requires GEMINI_API_KEY from https://aistudio.google.com (free, no credit card). Models: gemini/gemini-2.5-flash, gemini/gemini-2.0-flash",
    "anthropic": "Requires ANTHROPIC_API_KEY. Models: claude-sonnet-4-20250514, claude-haiku-4-5-20251001",
    "openai":    "Requires OPENAI_API_KEY. Models: gpt-4o, gpt-4o-mini",
    "ollama":    "Requires Ollama running locally (https://ollama.com). Models: llama3.2, mistral, gemma3",
    "groq":      "Requires GROQ_API_KEY. Models: llama-3.3-70b-versatile, mixtral-8x7b-32768",
    "mistral":   "Requires MISTRAL_API_KEY. Models: mistral-large-latest, mistral-small-latest",
}

DEFAULT_MODELS = {
    "gemini":    "gemini/gemini-2.5-flash",
    "anthropic": "claude-sonnet-4-20250514",
    "openai":    "gpt-4o-mini",
    "ollama":    "llama3.2",
    "groq":      "llama-3.3-70b-versatile",
    "mistral":   "mistral-small-latest",
}

# Environment variable that signals each cloud provider is configured
_API_KEY_ENV = {
    "gemini":    "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai":    "OPENAI_API_KEY",
    "groq":      "GROQ_API_KEY",
    "mistral":   "MISTRAL_API_KEY",
}


def _detect_provider() -> str:
    """
    Auto-detect the best available provider. Priority:
      1. Ollama — if the local server is reachable (free, no key, works offline)
      2. Cloud provider whose API key is present in the environment
      3. Raise a clear, actionable error

    This avoids the failure mode of silently defaulting to Anthropic when no
    key is configured, which causes every LLM call to fail and every disclosure
    to fall back to keyword-only detection (producing meaningless 50/100 scores).
    """
    import urllib.request

    # 1. Prefer Ollama — local, free, no key needed
    ollama_base = _ollama_base_url()
    try:
        urllib.request.urlopen(f"{ollama_base}/api/tags", timeout=1)
        return "ollama"
    except Exception:
        pass

    # 2. Fall back to whichever cloud key is present (best free tier first)
    for provider in ("gemini", "groq", "mistral", "anthropic", "openai"):
        if os.environ.get(_API_KEY_ENV[provider], "").strip():
            return provider

    # 3. Nothing available — give an actionable error
    raise ValueError(
        "No LLM provider detected. Choose one of:\n\n"
        "  Option A — Free cloud API (recommended):\n"
        "    1. Get a free key at https://aistudio.google.com (no credit card)\n"
        "    2. Add to .env:  GEMINI_API_KEY=AIza...\n"
        "    3. python main.py --pdf report.pdf --provider gemini\n\n"
        "  Option B — Local (no API key, requires Ollama):\n"
        "    ollama serve && ollama pull qwen2.5:14b\n"
        "    python main.py --pdf report.pdf --provider ollama --model qwen2.5:14b\n\n"
        "  Option C — Other cloud APIs, add one of these to your .env:\n"
        "    ANTHROPIC_API_KEY=sk-ant-...\n"
        "    OPENAI_API_KEY=sk-...\n"
        "    GROQ_API_KEY=gsk_...\n\n"
        "  Run 'python main.py --providers' for full provider documentation."
    )


def _ollama_base_url() -> str:
    return os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")


def _probe_ollama(base_url: str, timeout: float = 1.0) -> bool:
    import urllib.request

    try:
        urllib.request.urlopen(f"{base_url}/api/tags", timeout=timeout)
        return True
    except Exception:
        return False


def get_llm_status(config: "LLMConfig") -> dict:
    """
    Lightweight connectivity/configuration check for UI/reporting.
    - Ollama: verify reachability via /api/tags
    - Cloud providers: confirm API key presence (connectivity not verified)
    """
    provider = config.provider
    model = config.model

    status = {
        "provider": provider,
        "model": model,
        "state": "unknown",
        "detail": "",
        "verified": False,
    }

    if provider == "ollama":
        base = _ollama_base_url()
        ok = _probe_ollama(base)
        status.update(
            {
                "state": "connected" if ok else "unreachable",
                "detail": f"Ollama @ {base}",
                "verified": True,
                "base_url": base,
            }
        )
        return status

    key_env = _API_KEY_ENV.get(provider, "")
    has_key = bool(os.environ.get(key_env, "").strip()) if key_env else False
    if has_key:
        status.update(
            {
                "state": "configured",
                "detail": f"{key_env} present" if key_env else "API key present",
                "verified": False,
            }
        )
    else:
        status.update(
            {
                "state": "missing_key",
                "detail": f"{key_env} missing" if key_env else "API key missing",
                "verified": False,
            }
        )
    return status


# ── Config ─────────────────────────────────────────────────────────────────────

class LLMConfig:
    """
    Resolved LLM configuration.

    Resolution order:
      1. Explicit --provider / --model CLI arguments
      2. LLM_PROVIDER / LLM_MODEL environment variables (.env)
      3. Auto-detection: Ollama if running locally, else first cloud key found

    This means the tool works out of the box for Ollama users without any
    configuration, and for cloud users as soon as they add one key to .env.
    """

    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        max_retries: int = 5,
        retry_delay: float = 2.0,
        timeout: float = 60.0,
    ) -> None:
        # Resolve provider: CLI arg > env var > auto-detect
        raw_provider = provider or os.getenv("LLM_PROVIDER", "").strip()
        self.provider = raw_provider.lower() if raw_provider else _detect_provider()

        self.model = model or os.getenv("LLM_MODEL", "").strip() or DEFAULT_MODELS.get(self.provider, "")
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.timeout = timeout

        if not self.model:
            raise ValueError(
                f"No model configured for provider '{self.provider}'. "
                f"Set LLM_MODEL in your .env or pass --model explicitly."
            )

        # litellm model string format + provider-specific tuning
        if self.provider == "ollama":
            self.litellm_model = f"ollama/{self.model}"
            # Local models on CPU can be slow — use a generous timeout
            if self.timeout == 60.0:   # only override if user left it at default
                self.timeout = 300.0
        else:
            self.litellm_model = self.model  # litellm recognises claude-/gpt- prefixes

        logger.info("LLM provider: %s | model: %s", self.provider, self.model)

    @property
    def recommended_concurrency(self) -> int:
        """
        Safe default concurrency for this provider.

        Ollama runs inference sequentially — sending 6 parallel requests to a
        14B model causes connection timeouts as the server queues and times out
        requests. Use 1 for local models; cloud APIs can handle more.

        Users can always override with --concurrent.
        """
        if self.provider == "ollama":
            return 1
        if self.provider == "gemini":
            # Free tier: 5 RPM for Gemini 2.5 Flash — use 1 concurrent + smart retry
            return 1
        # Cloud APIs: conservative default that works on most free/starter tiers
        return 6

    def __repr__(self) -> str:
        return f"LLMConfig(provider={self.provider!r}, model={self.model!r})"

    @classmethod
    def list_providers(cls) -> str:
        lines = ["Supported providers:\n"]
        for name, doc in _PROVIDER_DOCS.items():
            lines.append(f"  {name:<12} {doc}")
        lines.append(
            "\nAuto-detection order (when no --provider is given):\n"
            "  1. Ollama (local, if running)  2. Gemini  3. Groq  4. Mistral  5. Anthropic  6. OpenAI"
        )
        return "\n".join(lines)




def _parse_retry_delay(error_str: str) -> float:
    """
    Extract the suggested retry delay from a rate-limit error message.
    Gemini returns: "Please retry in 45.479385067s"
    Falls back to 0 (caller uses exponential backoff) if not found.
    """
    import re as _re
    m = _re.search(r'retry[^0-9]{0,30}(\d+(?:\.\d+)?)\s*s', error_str, _re.IGNORECASE)
    if m:
        return min(float(m.group(1)) + 1.0, 120.0)  # cap at 2 minutes
    return 0.0


# ── Synchronous call (used by --check and keyword fallback) ────────────────────

def call_llm(
    system_prompt: str,
    user_prompt: str,
    config: LLMConfig,
) -> str:
    """
    Send a prompt to the configured LLM and return the response text.
    Retries on transient errors with exponential backoff.
    Raises LLMError on unrecoverable failure.
    """
    last_error: Exception = Exception("Unknown error")

    for attempt in range(1, config.max_retries + 1):
        try:
            api_base = _ollama_base_url() if config.provider == "ollama" else None
            response = litellm.completion(
                model=config.litellm_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                max_tokens=2048,
                timeout=config.timeout,
                api_base=api_base,
            )
            content = response.choices[0].message.content
            if content is None:
                raise LLMError("LLM returned empty content (None)")
            return content.strip()

        except litellm.RateLimitError as e:
            last_error = e
            # Honour the retryDelay the API tells us to use (Gemini returns this explicitly)
            wait = _parse_retry_delay(str(e)) or config.retry_delay * (2 ** (attempt - 1))
            logger.warning(
                "Rate limited (attempt %d/%d). Retrying in %.1fs…",
                attempt, config.max_retries, wait,
            )
            time.sleep(wait)

        except litellm.APIConnectionError as e:
            last_error = e
            if config.provider == "ollama":
                raise LLMError(
                    "Cannot connect to Ollama. Is it running? Start it with: ollama serve"
                ) from e
            wait = config.retry_delay * attempt
            logger.warning(
                "Connection error (attempt %d/%d). Retrying in %.1fs…",
                attempt, config.max_retries, wait,
            )
            time.sleep(wait)

        except litellm.AuthenticationError as e:
            raise LLMError(
                f"Authentication failed for provider '{config.provider}'. "
                f"Check your API key in .env.\n{_PROVIDER_DOCS.get(config.provider, '')}"
            ) from e

        except LLMError:
            raise   # don't wrap our own errors

        except Exception as e:
            last_error = e
            logger.warning("LLM call failed (attempt %d/%d): %s", attempt, config.max_retries, e)
            time.sleep(config.retry_delay)

    raise LLMError(
        f"LLM call failed after {config.max_retries} attempts: {last_error}"
    ) from last_error


# ── Async call (used by detect_all for concurrent disclosure checks) ───────────

async def call_llm_async(
    system_prompt: str,
    user_prompt: str,
    config: LLMConfig,
) -> str:
    """
    Async version of call_llm using litellm.acompletion.
    Semantically identical to call_llm — same retry logic, same error handling.
    Called by detect_all() via asyncio to run multiple disclosure checks
    concurrently (semaphore-controlled to respect API rate limits).
    """
    last_error: Exception = Exception("Unknown error")

    for attempt in range(1, config.max_retries + 1):
        try:
            api_base = _ollama_base_url() if config.provider == "ollama" else None
            response = await litellm.acompletion(
                model=config.litellm_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                max_tokens=2048,
                timeout=config.timeout,
                api_base=api_base,
            )
            content = response.choices[0].message.content
            if content is None:
                raise LLMError("LLM returned empty content (None)")
            return content.strip()

        except litellm.RateLimitError as e:
            last_error = e
            wait = _parse_retry_delay(str(e)) or config.retry_delay * (2 ** (attempt - 1))
            logger.warning(
                "Rate limited (attempt %d/%d). Retrying in %.1fs…",
                attempt, config.max_retries, wait,
            )
            await asyncio.sleep(wait)

        except litellm.APIConnectionError as e:
            last_error = e
            if config.provider == "ollama":
                raise LLMError(
                    "Cannot connect to Ollama. Is it running? Start it with: ollama serve"
                ) from e
            wait = config.retry_delay * attempt
            logger.warning(
                "Connection error (attempt %d/%d). Retrying in %.1fs…",
                attempt, config.max_retries, wait,
            )
            await asyncio.sleep(wait)

        except litellm.AuthenticationError as e:
            raise LLMError(
                f"Authentication failed for provider '{config.provider}'. "
                f"Check your API key in .env.\n{_PROVIDER_DOCS.get(config.provider, '')}"
            ) from e

        except LLMError:
            raise

        except Exception as e:
            last_error = e
            logger.warning(
                "Async LLM call failed (attempt %d/%d): %s", attempt, config.max_retries, e
            )
            await asyncio.sleep(config.retry_delay)

    raise LLMError(
        f"LLM call failed after {config.max_retries} attempts: {last_error}"
    ) from last_error


# ── Custom exception ───────────────────────────────────────────────────────────

class LLMError(Exception):
    """Raised when the LLM provider fails after all retries."""
