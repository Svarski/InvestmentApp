"""
Lightweight FastAPI auth layer in front of Streamlit.

Proxies HTTP and WebSocket to STREAMLIT_ORIGIN (default http://127.0.0.1:8501).
Credentials: APP_USERNAME, APP_PASSWORD (environment variables only).
"""

from __future__ import annotations

import asyncio
import os
import secrets
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
import websockets
from fastapi import FastAPI, Form, Request, Response, WebSocket, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
# --- Configuration (env only; never hardcode secrets) ---

SESSION_COOKIE_NAME = os.getenv("AUTH_SESSION_COOKIE", "app_session")
SESSION_COOKIE_VALUE = os.getenv("AUTH_SESSION_VALUE", "valid")

STREAMLIT_ORIGIN = os.getenv("STREAMLIT_ORIGIN", "http://127.0.0.1:8501").rstrip("/")

# When behind HTTPS (e.g. Nginx), set AUTH_COOKIE_SECURE=true
AUTH_COOKIE_SECURE = os.getenv("AUTH_COOKIE_SECURE", "false").lower() in ("1", "true", "yes")

# Max session lifetime (seconds); omit for session cookie (browser close)
SESSION_MAX_AGE: int | None
_sma = os.getenv("AUTH_SESSION_MAX_AGE")
if _sma and _sma.isdigit():
    SESSION_MAX_AGE = int(_sma)
else:
    SESSION_MAX_AGE = None

HOP_BY_HOP_REQUEST = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
    }
)

HOP_BY_HOP_RESPONSE = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
    }
)

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


def _upstream_http_url(path: str, query: str) -> str:
    p = path.lstrip("/")
    base = f"{STREAMLIT_ORIGIN}/{p}" if p else f"{STREAMLIT_ORIGIN}/"
    return f"{base}?{query}" if query else base


def _upstream_ws_url(path: str, query: bytes) -> str:
    origin = STREAMLIT_ORIGIN.replace("https://", "wss://").replace("http://", "ws://")
    p = path.lstrip("/")
    url = f"{origin}/{p}" if p else f"{origin}/"
    if query:
        q = query.decode("latin-1")
        url = f"{url}?{q}"
    return url


def _expected_credentials() -> tuple[str, str]:
    return os.getenv("APP_USERNAME", ""), os.getenv("APP_PASSWORD", "")


def credentials_configured() -> bool:
    u, p = _expected_credentials()
    return bool(u and p)


def verify_credentials(username: str, password: str) -> bool:
    if not credentials_configured():
        return False
    good_u, good_p = _expected_credentials()
    u_ok = secrets.compare_digest(username.encode("utf-8"), good_u.encode("utf-8"))
    p_ok = secrets.compare_digest(password.encode("utf-8"), good_p.encode("utf-8"))
    return u_ok and p_ok


def session_valid(request: Request) -> bool:
    return request.cookies.get(SESSION_COOKIE_NAME) == SESSION_COOKIE_VALUE


def strip_auth_cookie(cookie_header: str | None) -> str | None:
    if not cookie_header:
        return None
    parts = [p.strip() for p in cookie_header.split(";") if p.strip()]
    filtered = [p for p in parts if not p.startswith(f"{SESSION_COOKIE_NAME}=")]
    if not filtered:
        return None
    return "; ".join(filtered)


def forward_request_headers(request: Request) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in request.headers.items():
        kl = key.lower()
        if kl in HOP_BY_HOP_REQUEST:
            continue
        if kl == "cookie":
            stripped = strip_auth_cookie(value)
            if stripped:
                out[key] = stripped
            continue
        out[key] = value

    # Help Streamlit behind a reverse proxy
    host = request.headers.get("host", "")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    client = request.client.host if request.client else ""
    out["X-Forwarded-Host"] = host
    out["X-Forwarded-Proto"] = proto
    if client:
        prior = request.headers.get("x-forwarded-for")
        out["X-Forwarded-For"] = f"{client}, {prior}" if prior else client

    parsed = httpx.URL(STREAMLIT_ORIGIN)
    if parsed.port:
        out["Host"] = f"{parsed.host}:{parsed.port}"
    else:
        out["Host"] = parsed.host
    return out


def clean_response_headers(headers: httpx.Headers) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in headers.items():
        kl = key.lower()
        if kl in HOP_BY_HOP_RESPONSE:
            continue
        if kl == "content-length":
            continue
        out[key] = value
    return out


def rewrite_location(value: str) -> str:
    """Rewrite redirect Location from upstream origin to a path our clients can follow."""
    if value.startswith(STREAMLIT_ORIGIN):
        rest = value[len(STREAMLIT_ORIGIN) :]
        return rest if rest.startswith("/") else "/" + rest
    return value


@asynccontextmanager
async def lifespan(app: FastAPI):
    timeout = httpx.Timeout(connect=30.0, read=300.0, write=300.0, pool=5.0)
    app.state.http = httpx.AsyncClient(timeout=timeout, follow_redirects=False)
    yield
    await app.state.http.aclose()


app = FastAPI(title="Streamlit auth gateway", lifespan=lifespan)


@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request) -> Response:
    if session_valid(request):
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)
    err = None
    if not credentials_configured():
        err = "Login is not configured (set APP_USERNAME and APP_PASSWORD)."
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": err},
        status_code=status.HTTP_200_OK,
    )


@app.post("/login")
async def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
) -> Response:
    if not credentials_configured():
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Login is not configured (set APP_USERNAME and APP_PASSWORD).",
            },
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    if not verify_credentials(username.strip(), password):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid username or password."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    resp = RedirectResponse("/", status_code=status.HTTP_302_FOUND)
    resp.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=SESSION_COOKIE_VALUE,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite="lax",
        max_age=SESSION_MAX_AGE,
        path="/",
    )
    return resp


@app.get("/logout")
async def logout() -> Response:
    resp = RedirectResponse("/login", status_code=status.HTTP_302_FOUND)
    resp.delete_cookie(SESSION_COOKIE_NAME, path="/", secure=AUTH_COOKIE_SECURE)
    return resp


async def _proxy_http(request: Request, path: str) -> Response:
    if not session_valid(request):
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)

    client: httpx.AsyncClient = request.app.state.http
    url = _upstream_http_url(path, request.url.query)
    body = await request.body()
    headers = forward_request_headers(request)

    req = client.build_request(request.method, url, headers=headers, content=body)
    try:
        response = await client.send(req, stream=True)
    except httpx.ConnectError:
        return PlainTextResponse(
            "Cannot reach Streamlit upstream. Is it running on STREAMLIT_ORIGIN?",
            status_code=status.HTTP_502_BAD_GATEWAY,
        )
    except httpx.RequestError:
        return PlainTextResponse(
            "Upstream request failed.",
            status_code=status.HTTP_502_BAD_GATEWAY,
        )

    loc = response.headers.get("location")
    if loc and response.status_code in (301, 302, 303, 307, 308):
        loc = rewrite_location(loc)
        clean = clean_response_headers(response.headers)
        clean["location"] = loc
        await response.aclose()
        return Response(status_code=response.status_code, headers=clean)

    async def stream_with_cleanup() -> AsyncIterator[bytes]:
        try:
            async for chunk in response.aiter_raw():
                yield chunk
        finally:
            await response.aclose()

    return StreamingResponse(
        stream_with_cleanup(),
        status_code=response.status_code,
        headers=clean_response_headers(response.headers),
    )


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
)
async def proxy_catchall(request: Request, path: str) -> Response:
    return await _proxy_http(request, path)


def _ws_session_ok(websocket: WebSocket) -> bool:
    return websocket.cookies.get(SESSION_COOKIE_NAME) == SESSION_COOKIE_VALUE


async def _proxy_websocket(websocket: WebSocket, path: str) -> None:
    if not _ws_session_ok(websocket):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()

    uri = _upstream_ws_url(path, websocket.scope.get("query_string") or b"")
    cookie_in = websocket.headers.get("cookie")
    extra: list[tuple[str, str]] = []
    stripped = strip_auth_cookie(cookie_in)
    if stripped:
        extra.append(("Cookie", stripped))

    subprotocols = list(websocket.scope.get("subprotocols") or [])

    try:
        async with websockets.connect(
            uri,
            additional_headers=extra,
            subprotocols=subprotocols if subprotocols else None,
            max_size=None,
        ) as upstream:
            async def client_to_upstream() -> None:
                try:
                    while True:
                        msg = await websocket.receive()
                        mtype = msg.get("type")
                        if mtype == "websocket.disconnect":
                            break
                        if "text" in msg:
                            await upstream.send(msg["text"])
                        elif "bytes" in msg:
                            await upstream.send(msg["bytes"])
                except Exception:
                    pass

            async def upstream_to_client() -> None:
                try:
                    async for message in upstream:
                        if isinstance(message, str):
                            await websocket.send_text(message)
                        else:
                            await websocket.send_bytes(message)
                except Exception:
                    pass

            await asyncio.gather(client_to_upstream(), upstream_to_client())
    except OSError:
        await websocket.close(code=1011)
    except Exception:
        await websocket.close(code=1011)


@app.websocket("/{path:path}")
async def websocket_proxy(websocket: WebSocket, path: str) -> None:
    await _proxy_websocket(websocket, path)
