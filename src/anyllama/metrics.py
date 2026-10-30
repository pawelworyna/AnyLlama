"""Small, explicit request metrics. Unknown measurements stay unknown."""

from decimal import Decimal, InvalidOperation
from math import isfinite

PRICE_FIELDS = (
    "input_price_per_million",
    "output_price_per_million",
    "cached_input_price_per_million",
    "cache_write_price_per_million",
)


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() and result >= 0 and isfinite(float(result)) else None


def finite_float(value):
    result = float(value)
    return result if isfinite(result) else None


def token_count(value):
    result = number(value)
    return int(result) if result is not None and result <= 2**63 - 1 and result == int(result) else None


def catalog_prices(kind, model, metadata):
    prices = {}
    if kind == "tensorx":
        names = (
            "input_cost_per_token",
            "output_cost_per_token",
            "cache_read_input_token_cost",
            "cache_creation_input_token_cost",
        )
        prices = dict(zip(PRICE_FIELDS, (metadata.get(key) for key in names)))
    elif kind == "openrouter":
        pricing = model.get("pricing") or {}
        if not isinstance(pricing, dict):
            pricing = {}
        prices = dict(
            zip(
                PRICE_FIELDS,
                (
                    pricing.get(key)
                    for key in ("prompt", "completion", "input_cache_read", "input_cache_write")
                ),
            )
        )
    return {
        field: finite_float(value * 1_000_000) if (value := number(prices.get(field))) is not None else None
        for field in PRICE_FIELDS
    }


def estimated_cost(model, prompt, output, cached, written):
    if prompt is None or output is None:
        return None
    if not any(model.get(key) is not None for key in PRICE_FIELDS):
        return None
    cached, written = cached or 0, written or 0
    if cached + written > prompt:
        return None
    counts = (prompt - cached - written, output, cached, written)
    total = Decimal(0)
    for key, count in zip(PRICE_FIELDS, counts):
        if not count:
            continue
        rate = number(model.get(key))
        if rate is None:
            return None
        total += rate * count / 1_000_000
    return finite_float(total)


def request_statistics(
    *,
    usage,
    model,
    provider_kind,
    streamed,
    first_token_ms=None,
    last_token_ms=None,
    native_timings=None,
    status=200,
):
    prompt = token_count(usage.get("prompt_tokens"))
    output = token_count(usage.get("completion_tokens"))
    prompt_details = usage.get("prompt_tokens_details") or {}
    output_details = usage.get("completion_tokens_details") or {}
    prompt_details = prompt_details if isinstance(prompt_details, dict) else {}
    output_details = output_details if isinstance(output_details, dict) else {}
    cached = token_count(prompt_details.get("cached_tokens"))
    if cached is None and provider_kind == "deepseek":
        cached = token_count(usage.get("prompt_cache_hit_tokens"))
    written = token_count(prompt_details.get("cache_write_tokens"))
    reasoning = token_count(output_details.get("reasoning_tokens"))
    tokens_per_second = generation_ms = throughput_source = None
    native_duration = token_count((native_timings or {}).get("eval_duration"))
    reported_speed = number(usage.get("decoding_speed_tps")) if provider_kind == "fastflowlm" else None
    if status == 200 and output is not None and output > 0:
        if native_duration is not None and native_duration > 0:
            generation_ms = float(native_duration / 1_000_000)
            tokens_per_second = output * 1000 / generation_ms
            throughput_source = "provider"
        elif reported_speed is not None and reported_speed > 0:
            tokens_per_second = float(reported_speed)
            generation_ms = finite_float(Decimal(output) * 1000 / reported_speed)
            throughput_source = "provider"
        elif streamed and output > 1 and first_token_ms is not None and last_token_ms is not None:
            elapsed = last_token_ms - first_token_ms
            if elapsed > 0:
                generation_ms = elapsed
                tokens_per_second = (output - 1) * 1000 / elapsed
                throughput_source = "stream"
    reported = number(usage.get("cost"))
    known_currency = provider_kind in {"openrouter", "tensorx"} or usage.get("currency") == "USD"
    if reported is not None and known_currency:
        cost, source = float(reported), "reported"
    else:
        cost = estimated_cost(model, prompt, output, cached, written) if status == 200 else None
        source = "estimate" if cost is not None else None
    return {
        "streamed": bool(streamed),
        "generation_ms": generation_ms,
        "tokens_per_second": tokens_per_second,
        "throughput_source": throughput_source,
        "cached_input_tokens": cached,
        "reasoning_tokens": reasoning,
        "cost_usd": cost,
        "cost_source": source,
    }
