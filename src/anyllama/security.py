"""Bound request bodies before framework parsers buffer them."""

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

REQUEST_LIMIT = 32 * 1024 * 1024


class RequestLimitMiddleware:
    def __init__(self, app, limit=REQUEST_LIMIT):
        self.app = app
        self.limit = limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def reject():
            native = scope["path"].startswith("/api/")
            message = "Request exceeds the configured size limit"
            error = (
                message
                if native
                else {"message": message, "type": "request_too_large", "code": "request_too_large"}
            )
            return await JSONResponse({"error": error}, status_code=413)(scope, receive, send)

        declared = Headers(scope=scope).get("content-length")
        if declared and declared.isdigit() and int(declared) > self.limit:
            return await reject()
        received = 0
        chunks = []
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            received += len(chunk)
            if received > self.limit:
                return await reject()
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        del chunks
        pending = True

        async def limited_receive():
            nonlocal pending
            if pending:
                pending = False
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, limited_receive, send)
