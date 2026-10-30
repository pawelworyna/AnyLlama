import json
import sqlite3
import time
from types import SimpleNamespace

import httpx
import pytest
from conftest import chunk, completion, login

import anyllama.app as app_module
from anyllama.metrics import catalog_prices, request_statistics, token_count
from anyllama.storage import Store


def stats(usage=None, **kwargs):
    return request_statistics(
        usage=usage or {},
        model=kwargs.pop("model", {}),
        provider_kind=kwargs.pop("provider_kind", "openrouter"),
        streamed=kwargs.pop("streamed", True),
        **kwargs,
    )


@pytest.mark.parametrize(
    "kind,model,metadata",
    [
        (
            "openrouter",
            {"pricing": {"prompt": "0.0000004", "completion": "0.0000024", "input_cache_read": "0.0000001"}},
            {},
        ),
        (
            "tensorx",
            {},
            {
                "input_cost_per_token": 4e-7,
                "output_cost_per_token": 2.4e-6,
                "cache_read_input_token_cost": 1e-7,
            },
        ),
    ],
)
def test_catalog_rates_are_per_million(kind, model, metadata):
    rates = catalog_prices(kind, model, metadata)
    assert rates == {
        "input_price_per_million": 0.4,
        "output_price_per_million": 2.4,
        "cached_input_price_per_million": 0.1,
        "cache_write_price_per_million": None,
    }


@pytest.mark.parametrize("value", [None, -1, True, "NaN", "Infinity", "bad", {}, 2**64, 1.5])
def test_invalid_token_counts_are_unknown(value):
    assert token_count(value) is None


def test_reported_cost_wins_and_zero_is_not_unknown():
    for cost in (0, "0.000123"):
        result = stats({"cost": cost}, model={"input_price_per_million": 100})
        assert result["cost_usd"] == float(cost)
        assert result["cost_source"] == "reported"
    assert stats({"cost": 2}, provider_kind="custom")["cost_usd"] is None
    assert stats({"cost": 2, "currency": "USD"}, provider_kind="custom")["cost_usd"] == 2


def test_estimate_prices_cached_tokens_once_and_reasoning_is_subset():
    usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 200,
        "prompt_tokens_details": {"cached_tokens": 400, "cache_write_tokens": 100},
        "completion_tokens_details": {"reasoning_tokens": 150},
    }
    model = {
        "input_price_per_million": 2,
        "output_price_per_million": 8,
        "cached_input_price_per_million": 0.5,
        "cache_write_price_per_million": 3,
    }
    result = stats(usage, model=model)
    assert result["cost_usd"] == pytest.approx(0.0031)
    assert result["cost_source"] == "estimate"
    assert result["reasoning_tokens"] == 150 and result["cached_input_tokens"] == 400
    del model["cache_write_price_per_million"]
    assert stats(usage, model=model)["cost_usd"] is None
    assert stats(usage, model=model, status=499)["cost_usd"] is None
    usage["cost"] = 0.001
    assert stats(usage, status=502)["cost_usd"] == 0.001


def test_unknown_free_invalid_and_incomplete_usage():
    rates = {"input_price_per_million": 0, "output_price_per_million": 0}
    assert stats({"prompt_tokens": 20, "completion_tokens": 10}, model=rates)["cost_usd"] == 0
    assert stats({"completion_tokens": 10}, model=rates)["cost_usd"] is None
    assert (
        stats(
            {"prompt_tokens": 1, "completion_tokens": 1, "prompt_tokens_details": {"cached_tokens": 2}},
            model=rates,
        )["cost_usd"]
        is None
    )
    assert stats({"cost": "NaN"})["cost_usd"] is None
    assert stats({"prompt_tokens_details": "invalid"})["cached_input_tokens"] is None


def test_throughput_measured_and_unknown_cases():
    usage = {"completion_tokens": 101}
    result = stats(usage, first_token_ms=500, last_token_ms=2500)
    assert result["tokens_per_second"] == 50 and result["throughput_source"] == "stream"
    result = stats(usage, streamed=False, native_timings={"eval_duration": 2_000_000_000})
    assert result["tokens_per_second"] == 50.5 and result["throughput_source"] == "provider"
    assert stats(usage, streamed=False)["tokens_per_second"] is None
    assert stats(usage, streamed=False, native_timings={"eval_duration": 1e-300})["tokens_per_second"] is None
    assert stats(usage, first_token_ms=20, last_token_ms=20)["tokens_per_second"] is None
    assert stats(usage, first_token_ms=20, last_token_ms=100, status=499)["tokens_per_second"] is None


@pytest.mark.parametrize("endpoint", ["/api/chat", "/v1/chat/completions"])
@pytest.mark.parametrize("kind", ["openrouter", "tensorx", "custom"])
def test_real_pipeline_ignores_role_usage_and_finish_delays(make_client, monkeypatch, endpoint, kind):
    clock = {"now": 0.0}
    monkeypatch.setattr(
        app_module, "time", SimpleNamespace(perf_counter=lambda: clock["now"], monotonic=time.monotonic)
    )
    frames = [
        (0.1, chunk({"role": "assistant"})),
        (0.5, chunk({"reasoning": "private reasoning"})),
        (
            2.5,
            chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "read", "arguments": "{}"},
                        }
                    ]
                }
            ),
        ),
        (3, chunk({}, "tool_calls")),
        (
            5,
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 1000,
                    "completion_tokens": 101,
                    "cost": 0.002,
                    "currency": "USD",
                    "completion_tokens_details": {"reasoning_tokens": 90},
                    "private_data": "do not persist",
                },
            },
        ),
    ]

    class TimedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for now, frame in frames:
                clock["now"] = now
                yield ("data: " + json.dumps(frame) + "\n\n").encode()
            yield b"data: [DONE]\n\n"

    def upstream(req):
        assert json.loads(req.content)["stream_options"]["include_usage"] is True
        return httpx.Response(200, stream=TimedStream())

    client, app, model, _ = make_client(upstream, kind=kind)
    response = client.post(
        endpoint,
        json={
            "model": model["id"],
            "stream": True,
            "messages": [{"role": "user", "content": "private prompt"}],
        },
    )
    assert response.status_code == 200
    row = app.state.store.activity()[0]
    assert row["first_token_ms"] == 500 and row["duration_ms"] == 5000
    assert row["tokens_per_second"] == 50 and row["cost_usd"] == 0.002
    assert row["reasoning_tokens"] == 90
    assert "private" not in json.dumps(row) and "read" not in json.dumps(row)


def test_nonstream_estimate_has_no_fake_first_token_or_speed(make_client):
    client, app, model, _ = make_client(lambda req: httpx.Response(200, json=completion()), kind="custom")
    app.state.store.patch_model(model["id"], {"input_price_per_million": 1, "output_price_per_million": 2})
    client.post(
        "/api/chat",
        json={"model": model["id"], "stream": False, "messages": [{"role": "user", "content": "Hi"}]},
    ).raise_for_status()
    row = app.state.store.activity()[0]
    assert row["first_token_ms"] is None and row["tokens_per_second"] is None
    assert row["cost_usd"] == 0.00002 and row["cost_source"] == "estimate"


def test_deepseek_cache_and_fastflow_reported_speed():
    result = stats(
        {"prompt_tokens": 1000, "completion_tokens": 200, "prompt_cache_hit_tokens": 400},
        provider_kind="deepseek",
        model={
            "input_price_per_million": 2,
            "output_price_per_million": 8,
            "cached_input_price_per_million": 0.5,
        },
    )
    assert result["cached_input_tokens"] == 400 and result["cost_usd"] == 0.003
    result = stats(
        {"completion_tokens": 100, "decoding_speed_tps": 25}, provider_kind="fastflowlm", streamed=False
    )
    assert result["tokens_per_second"] == 25 and result["generation_ms"] == 4000
    assert result["throughput_source"] == "provider"


@pytest.mark.parametrize("streamed", [False, True])
def test_native_generation_duration_is_retained(make_client, streamed):
    data = {
        "message": {"role": "assistant", "content": "OK"},
        "done": True,
        "eval_count": 20,
        "prompt_eval_count": 10,
        "eval_duration": 1_000_000_000,
    }

    def upstream(req):
        return httpx.Response(200, content=json.dumps(data) + "\n")

    client, app, model, _ = make_client(upstream, kind="ollama")
    response = client.post(
        "/v1/chat/completions",
        json={"model": model["id"], "stream": streamed, "messages": [{"role": "user", "content": "Hi"}]},
    )
    assert response.status_code == 200
    row = app.state.store.activity()[0]
    assert row["tokens_per_second"] == 20 and row["throughput_source"] == "provider"


def test_additive_migration_keeps_legacy_history_and_is_repeatable(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE activity (id TEXT, created_at TEXT, model TEXT, provider TEXT, "
            "status INTEGER, duration_ms REAL, first_token_ms REAL, input_tokens INTEGER, "
            "output_tokens INTEGER, error TEXT)"
        )
        db.execute("INSERT INTO activity VALUES('old','date','model','provider',200,100,20,2,3,NULL)")
        db.execute("PRAGMA user_version=1")
    for _ in range(2):
        store = Store(path)
        assert store.activity()[-1]["id"] == "old"
        assert store.activity()[-1]["cost_usd"] is None
        store.close()
    store = Store(path)
    store.record(
        model="new",
        provider="p",
        status=200,
        duration_ms=10,
        statistics={"cost_usd": 0, "cost_source": "reported", "prompt": "never saved"},
    )
    assert store.activity()[0]["cost_usd"] == 0
    assert len(store.activity()) == 2
    store.close()


def test_price_override_survives_refresh_and_invalid_rate_is_rejected(make_client):
    client, app, model, _ = make_client(
        lambda req: httpx.Response(
            200,
            json={
                "data": [{"id": "vendor/model", "pricing": {"prompt": "0.000001", "completion": "0.000002"}}]
            },
        )
    )
    headers = login(client)
    path = f"/admin/api/models/{model['id']}"
    assert client.patch(path, headers=headers, json={"output_price_per_million": -1}).status_code == 422
    client.patch(path, headers=headers, json={"output_price_per_million": 5})
    client.post("/admin/api/providers/connection1/refresh", headers=headers).raise_for_status()
    saved = app.state.store.model(model["id"])
    assert saved["input_price_per_million"] == 1 and saved["output_price_per_million"] == 5
