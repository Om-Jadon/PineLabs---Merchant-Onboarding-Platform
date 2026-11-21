from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


# Two maximum-size base64 uploads plus room for JSON and declared fields.
UPLOAD_REQUEST_LIMIT = 2 * 4_194_304 + 65_536
METADATA_REQUEST_LIMIT = 16_384


class HttpBoundary:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        headers = Headers(scope=scope)

        async def send_response(message):
            if message["type"] == "http.response.start":
                output = MutableHeaders(scope=message)
                output["X-Content-Type-Options"] = "nosniff"
                output["Referrer-Policy"] = "no-referrer"
                output["X-Frame-Options"] = "DENY"
                output["Cross-Origin-Resource-Policy"] = "same-origin"
                output.setdefault(
                    "Content-Security-Policy",
                    "frame-ancestors 'none'; object-src 'none'; base-uri 'none'",
                )
                if path.startswith("/api/"):
                    output["Cache-Control"] = "no-store"
                elif path in {"/", "/index.html"}:
                    output["Content-Security-Policy"] = (
                        "default-src 'self'; script-src 'self'; "
                        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; "
                        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
                    )
            await send(message)

        if scope["method"] != "POST":
            return await self.app(scope, receive, send_response)
        origin = headers.get("origin")
        same_origin = f"{scope['scheme']}://{headers.get('host', '')}"
        if headers.get("sec-fetch-site") == "cross-site" or (
            origin is not None and origin != same_origin
        ):
            return await JSONResponse({"detail": "Use the local application origin."}, 403)(
                scope, receive, send_response
            )
        upload = path == "/api/applications" or (
            path.startswith("/api/applications/") and path.endswith("/corrections")
        )
        limit = UPLOAD_REQUEST_LIMIT if upload else METADATA_REQUEST_LIMIT
        length = headers.get("content-length")
        if length is not None:
            if not length.isascii() or not length.isdigit() or len(length) > 20:
                return await JSONResponse({"detail": "Invalid request length."}, 400)(
                    scope, receive, send_response
                )
            if int(length) > limit:
                return await JSONResponse({"detail": "Request exceeds its size limit."}, 413)(
                    scope, receive, send_response
                )
        total = 0

        async def limited_receive():
            nonlocal total
            message = await receive()
            total += len(message.get("body", b""))
            if total > limit:
                raise HTTPException(413, "Request exceeds its size limit.")
            return message

        await self.app(scope, limited_receive, send_response)
