"""DashScope JSON chat via the same sanitized, server-configured HTTP transport."""

import json

from easyrag.retrieval.cloud_models import CloudModelError, CloudBackendUnavailable


def generate_json(models, messages, config):
    if config.model.startswith("glm-") and getattr(models, "provider", None) != "glm":
        raise CloudBackendUnavailable("GLM generation requires the console GLM backend")
    body = models._post("/compatible-mode/v1/chat/completions", {
        "model": config.model, "messages": messages, "temperature": config.temperature,
        "max_tokens": config.max_output_tokens, "enable_thinking": False,
        "response_format": {"type": "json_object"}, "stream": False,
    })
    choices = body.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise CloudModelError("Generation response choices are invalid")
    choice = choices[0]
    if choice.get("finish_reason") != "stop":
        raise CloudModelError("Generation did not finish normally; no partial work order accepted")
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip() or len(content) > 60000:
        raise CloudModelError("Generation response content is invalid or over budget")

    def no_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        result = json.loads(content, object_pairs_hook=no_duplicate_keys,
                            parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    except (ValueError, RecursionError):
        raise CloudModelError("Generation returned invalid JSON") from None
    if not isinstance(result, dict):
        raise CloudModelError("Generation must return one JSON object")
    # Only numeric usage counters are retained, never provider error messages/headers.
    usage = body.get("usage", {})
    usage = {k: v for k, v in usage.items() if k in {"prompt_tokens", "completion_tokens", "total_tokens"}
             and type(v) is int and v >= 0} if isinstance(usage, dict) else {}
    return result, usage
