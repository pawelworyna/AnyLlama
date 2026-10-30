import asyncio
import contextlib
import hashlib
import json
import secrets
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .codecs import Accumulator, decode_request, native_chunk, native_message, native_result
from .metrics import request_statistics, token_count
from .models import PRESETS, ModelInput, ModelPatch, ProviderInput, ProxyError, Settings
from .providers import adapter_for, transport_error
from .secrets import CredentialVault
from .security import RequestLimitMiddleware
from .storage import Store, utcnow
from .updates import UpdateChecker

ROOT = Path(__file__).parent


def error_response(protocol, exc):
    error = (
        exc.message if protocol == "ollama" else {"message": exc.message, "type": exc.code, "code": exc.code}
    )
    return JSONResponse({"error": error}, status_code=exc.status)


def create_app(
    data_dir: Path,
    *,
    vault=None,
    transport=None,
    bootstrap_token=None,
    refresh_interval=300,
    update_interval=300,
    apply_autostart=None,
) -> FastAPI:
    store = Store(data_dir / "anyllama.db")
    vault = vault if vault is not None else CredentialVault()
    bootstrap = bootstrap_token or secrets.token_urlsafe(32)
    sessions = {}
    refresh_locks = {}

    def adapter(provider):
        try:
            ProviderInput.clean_url(provider["base_url"])
        except ValueError as exc:
            raise ProxyError(
                400,
                "This saved connection needs an HTTPS API URL. Create a connection with a secure URL.",
                "invalid_provider_url",
            ) from exc
        try:
            key = vault.get(provider["id"]) if provider["has_key"] else None
        except Exception as exc:
            raise ProxyError(
                503,
                "Windows Credential Manager is unavailable. Unlock your Windows session and try again.",
                "credential_store_unavailable",
            ) from exc
        if provider["has_key"] and not key:
            raise ProxyError(401, "The saved key is missing. Re-enter it in Providers.", "missing_key")
        return adapter_for(provider, key, app.state.client, store.settings())

    async def refresh(provider_id):
        async with refresh_locks.setdefault(provider_id, asyncio.Lock()):
            provider = store.provider(provider_id)
            if not provider:
                raise ProxyError(404, "Connection not found", "not_found")
            try:
                catalog = [
                    ModelInput.model_validate(m).model_dump(exclude={"enabled"})
                    for m in await adapter(provider).catalog()
                ]
                store.refresh(provider_id, catalog)
                return {"count": len(catalog)}
            except httpx.HTTPError as exc:
                error = transport_error(exc)
                store.refresh(provider_id, [], error.message)
                raise error from exc
            except ProxyError as exc:
                store.refresh(provider_id, [], exc.message)
                raise
            except (ValueError, KeyError, TypeError) as exc:
                error = ProxyError(502, "The provider returned an invalid model catalog", "invalid_catalog")
                store.refresh(provider_id, [], error.message)
                raise error from exc

    async def refresh_loop():
        while True:
            await asyncio.gather(*(refresh(p["id"]) for p in store.providers()), return_exceptions=True)
            await asyncio.sleep(refresh_interval)

    async def update_loop():
        while True:
            if store.settings().check_updates:
                await app.state.updates.check()
            await asyncio.sleep(update_interval)

    @asynccontextmanager
    async def lifespan(application):
        async with httpx.AsyncClient(transport=transport, follow_redirects=False, trust_env=False) as client:
            application.state.client = client
            application.state.updates = UpdateChecker(client, store)
            task = asyncio.create_task(refresh_loop()) if refresh_interval > 0 else None
            updates_task = asyncio.create_task(update_loop()) if update_interval > 0 else None
            try:
                yield
            finally:
                for background_task in (task, updates_task):
                    if background_task:
                        background_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await background_task
                store.close()

    app = FastAPI(
        title="AnyLlama",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.store = store
    app.state.bootstrap_token = bootstrap
    app.state.paused = False
    app.state.bound_port = store.settings().port
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])
    app.add_middleware(RequestLimitMiddleware)

    @app.middleware("http")
    async def local_security(request: Request, call_next):
        protocol = "ollama" if request.url.path.startswith("/api/") else "openai"
        origin = request.headers.get("origin")
        expected_origin = f"{request.url.scheme}://{request.url.netloc}"
        if (origin and origin != expected_origin) or request.headers.get("sec-fetch-site") == "cross-site":
            return error_response(
                protocol, ProxyError(403, "Cross-site requests are not allowed", "forbidden")
            )
        if request.url.path.startswith("/admin/api/") and request.url.path != "/admin/api/session":
            session = sessions.get(request.cookies.get("anyllama_session"))
            if not session or time.monotonic() - session["created"] > 86400:
                return error_response(
                    "openai",
                    ProxyError(401, "Open the dashboard from the tray icon to sign in", "unauthorized"),
                )
            if request.method not in {"GET", "HEAD"} and not secrets.compare_digest(
                request.headers.get("x-csrf-token", ""), session["csrf"]
            ):
                return error_response(
                    "openai", ProxyError(403, "Invalid session token. Reopen the dashboard.", "forbidden")
                )
            request.state.csrf = session["csrf"]
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/admin"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            )
        return response

    @app.exception_handler(ProxyError)
    async def proxy_error(request, exc):
        return error_response("ollama" if request.url.path.startswith("/api/") else "openai", exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Validation errors can contain the rejected API key; do not echo input values.
        fields = ", ".join(".".join(str(p) for p in e["loc"][1:]) for e in exc.errors())
        return error_response("openai", ProxyError(422, f"Invalid value for: {fields}", "validation_error"))

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        code = "request_too_large" if exc.status_code == 413 else "http_error"
        return error_response(
            "ollama" if request.url.path.startswith("/api/") else "openai",
            ProxyError(exc.status_code, str(exc.detail), code),
        )

    async def body_json(request):
        try:
            raw = await request.body()
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError()
            return body
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProxyError(400, "Expected a JSON object", "invalid_json") from exc

    @app.get("/", response_class=HTMLResponse)
    async def root():
        return HTMLResponse("Ollama is running")

    @app.get("/api/version")
    async def version():
        return {"version": "0.18.0", "anyllama_version": __version__}

    @app.get("/api/ps")
    async def ps():
        return {"models": []}

    @app.get("/api/tags")
    async def tags():
        return {
            "models": [
                {
                    "name": m["id"],
                    "model": m["id"],
                    "modified_at": "2026-01-01T00:00:00Z",
                    "size": 0,
                    "digest": hashlib.sha256(m["id"].encode()).hexdigest(),
                    "details": {
                        "family": m["provider_kind"],
                        "families": [m["provider_kind"]],
                        "parameter_size": "unknown",
                        "quantization_level": "unknown",
                    },
                }
                for m in store.models(True)
            ]
        }

    @app.get("/v1/models")
    async def models():
        return {
            "object": "list",
            "data": [
                {"id": m["id"], "object": "model", "created": 0, "owned_by": m["provider_name"]}
                for m in store.models(True)
            ],
        }

    @app.post("/api/show")
    async def show(request: Request):
        body = await body_json(request)
        identifier = body.get("model") or body.get("name")
        if not isinstance(identifier, str) or not identifier:
            raise ProxyError(400, "A model name is required", "invalid_request")
        model = store.model(identifier, True)
        if not model:
            raise ProxyError(404, "Model not found or not enabled", "model_not_found")
        caps = ["completion"] + [
            cap
            for field, cap in (("tools", "tools"), ("vision", "vision"), ("reasoning", "thinking"))
            if model.get(field) is True
        ]
        info = {"general.architecture": "anyllama", "general.basename": model["name"]}
        if model.get("context_length"):
            info["anyllama.context_length"] = model["context_length"]
        return {
            "capabilities": caps,
            "details": {"family": "anyllama", "families": ["anyllama"]},
            "model_info": info,
            "parameters": "",
            "template": "{{ .Prompt }}",
            "modified_at": "2026-01-01T00:00:00Z",
        }

    async def inference(request: Request, protocol: str, generate=False):
        if app.state.paused:
            raise ProxyError(503, "AnyLlama is paused. Resume it in the dashboard or tray.", "proxy_paused")
        body = await body_json(request)
        try:
            data = decode_request(body, protocol, generate)
        except (KeyError, TypeError, AttributeError) as exc:
            raise ProxyError(400, "Invalid message or tool format", "invalid_request") from exc
        model = store.model(data["model"], True)
        if not model:
            raise ProxyError(404, "Model not found or not enabled", "model_not_found")
        if data.get("tools") and model.get("tools") is False:
            raise ProxyError(
                400,
                "This model does not support tools. Select a tool-capable model for Agent.",
                "unsupported_tools",
            )
        has_images = any(
            isinstance(m.get("content"), list) and any(p.get("type") == "image_url" for p in m["content"])
            for m in data["messages"]
        )
        if has_images and model.get("vision") is False:
            raise ProxyError(400, "This model does not support images", "unsupported_image")
        alias = model["id"]
        data["model"] = model["upstream_id"]
        provider = store.provider(model["provider_id"])
        if (
            provider["kind"] == "gemini"
            and protocol == "ollama"
            and (
                data.get("tools")
                or any(m.get("tool_calls") or m.get("role") == "tool" for m in data["messages"])
            )
        ):
            raise ProxyError(
                400,
                "Gemini tool calling requires the OpenAI-compatible /v1 endpoint and a client that preserves thought signatures. Ollama cannot preserve this replay state.",
                "unsupported_reasoning_replay",
            )
        started = time.perf_counter()
        usage_data = {}
        last_token_ms = None
        metrics = {
            "status": 200,
            "first_token_ms": None,
            "input_tokens": None,
            "output_tokens": None,
            "error": None,
        }

        def record():
            store.record(
                model=alias,
                provider=provider["name"],
                duration_ms=(time.perf_counter() - started) * 1000,
                statistics=request_statistics(
                    usage=usage_data,
                    model=model,
                    provider_kind=provider["kind"],
                    streamed=data["stream"],
                    first_token_ms=metrics["first_token_ms"],
                    last_token_ms=last_token_ms,
                    native_timings=upstream.timings if response is not None else {},
                    status=metrics["status"],
                ),
                **metrics,
            )

        response = None
        try:
            upstream = adapter(provider)
            response = await upstream.open_chat(data)
            if not data["stream"]:
                try:
                    completion = await upstream.completion(response)
                    completion["model"] = alias
                    usage = completion.get("usage") or {}
                    usage_data.update(usage)
                    metrics.update(
                        input_tokens=token_count(usage.get("prompt_tokens")),
                        output_tokens=token_count(usage.get("completion_tokens")),
                    )
                    result = (
                        native_result(completion, alias, int((time.perf_counter() - started) * 1e9), generate)
                        if protocol == "ollama"
                        else completion
                    )
                finally:
                    await response.aclose()
                record()
                return JSONResponse(result)
        except (httpx.HTTPError, ProxyError, ValueError, KeyError, TypeError) as exc:
            if response is not None:
                await response.aclose()
            error = (
                exc
                if isinstance(exc, ProxyError)
                else (
                    transport_error(exc)
                    if isinstance(exc, httpx.HTTPError)
                    else ProxyError(
                        502, "The provider returned an invalid response", "invalid_upstream_response"
                    )
                )
            )
            metrics.update(status=error.status, error=error.code)
            record()
            raise error from exc
        except asyncio.CancelledError:
            if response is not None:
                await response.aclose()
            metrics.update(status=499, error="client_disconnected")
            record()
            raise

        async def stream():
            nonlocal last_token_ms
            accumulator = Accumulator(alias, retain_content=False)
            try:
                async for chunk in upstream.chunks(response):
                    chunk["model"] = alias
                    accumulator.add(chunk)
                    if any(
                        (
                            c.get("delta", {}).get("content")
                            or c.get("delta", {}).get("tool_calls")
                            or c.get("delta", {}).get("reasoning")
                            or c.get("delta", {}).get("reasoning_content")
                        )
                        for c in chunk.get("choices", [])
                    ):
                        now_ms = (time.perf_counter() - started) * 1000
                        if metrics["first_token_ms"] is None:
                            metrics["first_token_ms"] = now_ms
                        last_token_ms = now_ms
                    if protocol == "openai":
                        yield "data: " + json.dumps(chunk) + "\n\n"
                    else:
                        converted = native_chunk(chunk, alias, generate)
                        if converted:
                            yield json.dumps(converted) + "\n"
                completion = accumulator.completion()
                if protocol == "ollama":
                    if accumulator.tools and not generate:
                        tool_message = native_message(
                            {"tool_calls": completion["choices"][0]["message"]["tool_calls"]}
                        )
                        yield (
                            json.dumps(
                                {
                                    "model": alias,
                                    "created_at": utcnow(),
                                    "done": False,
                                    "message": tool_message,
                                }
                            )
                            + "\n"
                        )
                        completion["choices"][0]["message"] = {"role": "assistant", "content": ""}
                    yield (
                        json.dumps(
                            native_result(
                                completion, alias, int((time.perf_counter() - started) * 1e9), generate
                            )
                        )
                        + "\n"
                    )
                else:
                    yield "data: [DONE]\n\n"
            except asyncio.CancelledError:
                metrics.update(status=499, error="client_disconnected")
                raise
            except Exception as exc:
                error = (
                    exc
                    if isinstance(exc, ProxyError)
                    else (
                        transport_error(exc)
                        if isinstance(exc, httpx.HTTPError)
                        else ProxyError(
                            502, "The provider returned invalid stream data", "invalid_upstream_stream"
                        )
                    )
                )
                metrics.update(status=error.status, error=error.code)
                if protocol == "ollama":
                    yield json.dumps({"error": error.message}) + "\n"
                else:
                    yield (
                        "data: "
                        + json.dumps({"error": {"message": error.message, "type": error.code}})
                        + "\n\n"
                    )
            finally:
                usage_data.update(accumulator.usage)
                metrics.update(
                    input_tokens=token_count(accumulator.usage.get("prompt_tokens")),
                    output_tokens=token_count(accumulator.usage.get("completion_tokens")),
                )
                record()
                await asyncio.shield(response.aclose())

        return StreamingResponse(
            stream(),
            media_type="application/x-ndjson" if protocol == "ollama" else "text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            background=BackgroundTask(response.aclose),
        )

    @app.post("/api/chat")
    async def chat(request: Request):
        return await inference(request, "ollama")

    @app.post("/api/generate")
    async def generate(request: Request):
        return await inference(request, "ollama", True)

    @app.post("/v1/chat/completions")
    @app.post("/chat/completions")
    async def completions(request: Request):
        return await inference(request, "openai")

    app.mount("/admin/static", StaticFiles(directory=ROOT / "static"), name="static")
    templates = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape())

    @app.get("/admin", response_class=HTMLResponse)
    @app.get("/admin/", response_class=HTMLResponse)
    async def dashboard():
        return templates.get_template("index.html").render(version=__version__)

    @app.post("/admin/api/session")
    async def login(request: Request):
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
        if not secrets.compare_digest(supplied, bootstrap):
            raise ProxyError(401, "Open the dashboard from the AnyLlama tray icon", "unauthorized")
        now = time.monotonic()
        for identifier in list(sessions):
            if now - sessions[identifier]["created"] > 86400:
                sessions.pop(identifier)
        identifier = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        sessions[identifier] = {"csrf": csrf, "created": now}
        response = JSONResponse({"csrf": csrf})
        response.set_cookie(
            "anyllama_session", identifier, httponly=True, samesite="strict", max_age=86400, path="/admin"
        )
        return response

    @app.get("/admin/api/state")
    async def state(request: Request):
        return {
            "version": __version__,
            "csrf": request.state.csrf,
            "paused": app.state.paused,
            "endpoint": f"http://127.0.0.1:{app.state.bound_port}",
            "presets": PRESETS,
            "providers": store.providers(),
            "models": store.models(),
            "activity": store.activity(),
            "settings": store.settings().model_dump(),
            "restart_required": store.settings().port != app.state.bound_port,
            "updates": app.state.updates.status(),
        }

    @app.post("/admin/api/updates/check")
    async def check_updates():
        return await app.state.updates.check(force=True)

    @app.get("/admin/api/indicator", response_class=HTMLResponse)
    async def indicator():
        return '<span class="small-dot"></span> Proxy ' + ("paused" if app.state.paused else "running")

    @app.post("/admin/api/providers")
    async def add_provider(data: ProviderInput):
        identifier = uuid.uuid4().hex[:12]
        key = data.api_key
        if PRESETS[data.kind].get("requires_key", True) and not key:
            raise ProxyError(400, "An API key is required for this provider", "missing_key")
        try:
            if key:
                vault.set(identifier, key)
        except Exception as exc:
            raise ProxyError(
                503, "Could not save the key in Windows Credential Manager", "credential_store_unavailable"
            ) from exc
        provider = store.save_provider(
            {**data.model_dump(exclude={"api_key"}), "has_key": bool(key)}, identifier
        )
        return provider

    @app.put("/admin/api/providers/{provider_id}")
    async def edit_provider(provider_id: str, data: ProviderInput):
        current = store.provider(provider_id)
        if not current:
            raise ProxyError(404, "Connection not found", "not_found")
        if current["kind"] != data.kind or current["base_url"] != data.base_url:
            raise ProxyError(
                400, "Create a new connection to change its provider type or API URL", "immutable_endpoint"
            )
        has_key = current["has_key"]
        if data.api_key is not None:
            if PRESETS[data.kind].get("requires_key", True) and not data.api_key:
                raise ProxyError(400, "An API key is required", "missing_key")
            try:
                vault.set(provider_id, data.api_key) if data.api_key else vault.delete(provider_id)
            except Exception as exc:
                raise ProxyError(
                    503,
                    "Could not update the key in Windows Credential Manager",
                    "credential_store_unavailable",
                ) from exc
            has_key = bool(data.api_key)
        return store.save_provider({**data.model_dump(exclude={"api_key"}), "has_key": has_key}, provider_id)

    @app.delete("/admin/api/providers/{provider_id}")
    async def delete_provider(provider_id: str):
        async with refresh_locks.setdefault(provider_id, asyncio.Lock()):
            current = store.provider(provider_id)
            if not current:
                raise ProxyError(404, "Connection not found", "not_found")
            try:
                if current["has_key"]:
                    vault.delete(provider_id)
            except Exception as exc:
                raise ProxyError(
                    503,
                    "Could not remove the key from Windows Credential Manager",
                    "credential_store_unavailable",
                ) from exc
            store.delete_provider(provider_id)
        return {"ok": True}

    @app.post("/admin/api/providers/{provider_id}/refresh")
    async def refresh_provider(provider_id: str):
        return await refresh(provider_id)

    @app.post("/admin/api/providers/{provider_id}/models")
    async def add_model(provider_id: str, data: ModelInput):
        if not store.provider(provider_id):
            raise ProxyError(404, "Connection not found", "not_found")
        try:
            return store.add_model(provider_id, data.model_dump())
        except sqlite3.IntegrityError as exc:
            raise ProxyError(409, "This model is already in the catalog", "duplicate_model") from exc

    @app.patch("/admin/api/models/{model_id:path}")
    async def edit_model(model_id: str, data: ModelPatch):
        patch = data.model_dump(exclude_unset=True)
        if patch.get("name", "") is None or ("enabled" in patch and patch["enabled"] is None):
            raise ProxyError(422, "Name and enabled cannot be null", "validation_error")
        result = store.patch_model(model_id, patch)
        if not result:
            raise ProxyError(404, "Model not found", "not_found")
        return result

    @app.put("/admin/api/settings")
    async def settings(data: Settings):
        if data.autostart != store.settings().autostart:
            if not apply_autostart:
                raise ProxyError(
                    400, "Autostart is available in the Windows desktop application", "autostart_unavailable"
                )
            try:
                apply_autostart(data.autostart)
            except OSError as exc:
                raise ProxyError(
                    500, "Windows could not update the autostart setting", "autostart_failed"
                ) from exc
        store.save_settings(data)
        return {"ok": True, "restart_required": data.port != app.state.bound_port}

    @app.post("/admin/api/pause")
    async def pause(request: Request):
        body = await body_json(request)
        app.state.paused = bool(body.get("paused", True))
        return {"paused": app.state.paused}

    @app.get("/admin/api/export")
    async def export():
        return JSONResponse(
            store.export(), headers={"Content-Disposition": 'attachment; filename="anyllama-config.json"'}
        )

    return app
