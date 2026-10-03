"""Class-based judgment routing. Legacy question and result contracts pass through.

System work follows the config; production runtime is permanently pinned to Jev.
The Jev transport is injected so its retries, caches and receipts stay intact.
No credential or provider endpoint is owned by this interface.
"""
import json
import sys
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[2] / "mcp-server/src/judge-providers.v1.json"
DECISIONS_UNAVAILABLE = "decisions contract not yet verified / no key"


class JudgeUnavailable(RuntimeError):
    """A routing or provider failure, never an affirmative judgment."""


def provider_for(work_class, config=None):
    config = json.loads(CONFIG.read_text()) if config is None else config
    if (not isinstance(config, dict) or config.get("schema") != "carr-judge-providers/v1"
            or not isinstance(config.get("providers"), dict)
            or set(config["providers"]) != {"system_work", "app_runtime"}):
        raise JudgeUnavailable("invalid judge provider config")
    if config["providers"]["app_runtime"] != "jev":
        raise JudgeUnavailable("app_runtime is pinned to jev; decisions routing is forbidden")
    if work_class not in ("system_work", "app_runtime"):
        raise JudgeUnavailable("unknown judge work class")
    provider = config["providers"][work_class]
    if provider not in ("jev", "decisions"):
        raise JudgeUnavailable("unknown judge provider")
    return provider


def provider_jev(state, questions, *, transport, **options):
    return transport(state, questions, **options)


def provider_decisions(state, questions, **options):
    # No endpoint, guessed wire contract, key read, fallback or network call.
    raise JudgeUnavailable(DECISIONS_UNAVAILABLE)


def ask(state, questions, *, jev, work_class="system_work", config=None, **options):
    provider = provider_for(work_class, config)
    # Legacy clients load this file by absolute path while sys.path contains
    # only ops/. Make the repository package available to the local capture.
    root = str(Path(__file__).resolve().parents[2])
    if root not in sys.path:
        sys.path.insert(0, root)
    from tools.judge import capture
    receipt = capture.prepare(state, questions, model=options.get("model", "jev-latest"),
                              caller=options.get("caller"), provider=provider, work_class=work_class)
    try:
        result = (provider_decisions(state, questions, **options) if provider == "decisions" else
                  provider_jev(state, questions, transport=jev, **options))
    except Exception:
        capture.append(receipt, failed=True)
        raise
    capture.append(receipt)
    return result
