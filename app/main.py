"""Caddy Manager — lightweight web UI + REST API + MCP for managing Caddy reverse-proxy domains."""

import glob
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager

import docker
from docker.errors import DockerException, NotFound
from fastapi import FastAPI, HTTPException, Request, Depends, Header
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("caddy-manager")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ADDON_DIR = os.environ.get("ADDON_DIR", "/data/addons")
CADDY_CONTAINER = os.environ.get("CADDY_CONTAINER", "caddy")
BASE_DOMAIN = os.environ.get("BASE_DOMAIN", "some-tools.org")
API_KEY = os.environ["API_KEY"]  # no default — must be set

# ---------------------------------------------------------------------------
# Docker client (lazy — socket mounted at /var/run/docker.sock)
# ---------------------------------------------------------------------------
_docker: docker.DockerClient | None = None


def get_docker() -> docker.DockerClient:
    global _docker
    if _docker is None:
        _docker = docker.from_env()
    return _docker


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def verify_api_key(x_api_key: str = Header(None), authorization: str = Header(None)):
    """Accept key via X-API-Key header or Bearer token."""
    key = x_api_key
    if not key and authorization and authorization.startswith("Bearer "):
        key = authorization[7:]
    if not key:
        raise HTTPException(401, "Missing API key")
    if not secrets.compare_digest(key, API_KEY):
        raise HTTPException(403, "Invalid API key")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
_SUBDOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
_CONTAINER_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]+$")


class DomainCreate(BaseModel):
    subdomain: str = Field(..., min_length=1, max_length=63)
    container: str = Field(..., min_length=1, max_length=128)
    port: int = Field(..., ge=1, le=65535)
    network: str = ""

    @field_validator("subdomain")
    @classmethod
    def validate_subdomain(cls, v: str) -> str:
        if not _SUBDOMAIN_RE.match(v):
            raise ValueError("Lowercase alphanumeric + hyphens only, no leading/trailing hyphen")
        return v

    @field_validator("container")
    @classmethod
    def validate_container(cls, v: str) -> str:
        if not _CONTAINER_RE.match(v):
            raise ValueError("Invalid container name")
        return v


# ---------------------------------------------------------------------------
# Service helpers
# ---------------------------------------------------------------------------
def _parse_addon_domains() -> list[dict]:
    """Parse all site-*.conf files in addon dir."""
    domains = []
    for fpath in sorted(glob.glob(os.path.join(ADDON_DIR, "site-*.conf"))):
        content = open(fpath).read()
        fname = os.path.basename(fpath)
        m_domain = re.search(r"([\w.-]+\." + re.escape(BASE_DOMAIN) + r")\s*\{", content)
        m_upstream = re.search(r"reverse_proxy\s+([\w.\-]+:\d+)", content)
        if m_domain:
            domain = m_domain.group(1)
            upstream = m_upstream.group(1) if m_upstream else "unknown"
            parts = upstream.rsplit(":", 1) if m_upstream else ["unknown", "0"]
            managed = "# Managed by Caddy Manager" in content
            domains.append({
                "file": fname,
                "domain": domain,
                "subdomain": domain.replace(f".{BASE_DOMAIN}", ""),
                "container": parts[0],
                "port": int(parts[1]) if len(parts) > 1 else 0,
                "upstream": upstream,
                "managed": managed,
            })
    return domains


def _write_domain_conf(subdomain: str, container: str, port: int) -> str:
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "-", subdomain)
    filename = f"site-{safe_name}.conf"
    filepath = os.path.join(ADDON_DIR, filename)
    content = f"""# Managed by Caddy Manager
{subdomain}.{BASE_DOMAIN} {{
    import service_tls
    reverse_proxy {container}:{port}
}}
"""
    with open(filepath, "w") as f:
        f.write(content)
    logger.info("Created %s -> %s:%d", subdomain, container, port)
    return filename


def _delete_domain_conf(subdomain: str) -> str:
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "-", subdomain)
    filename = f"site-{safe_name}.conf"
    filepath = os.path.join(ADDON_DIR, filename)
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"{filename} not found")
    os.remove(filepath)
    logger.info("Deleted %s", filename)
    return filename


def _reload_caddy() -> str:
    client = get_docker()
    caddy = client.containers.get(CADDY_CONTAINER)
    exit_code, output = caddy.exec_run("caddy reload --config /etc/caddy/Caddyfile")
    result = output.decode() if output else ("OK" if exit_code == 0 else f"Failed (exit {exit_code})")
    logger.info("Caddy reload: %s", result[:100])
    return result


def _connect_container_to_network(container_name: str, network_name: str) -> str:
    client = get_docker()
    container = client.containers.get(container_name)
    current_nets = list(container.attrs["NetworkSettings"]["Networks"].keys())
    if network_name in current_nets:
        return f"Already connected to {network_name}"
    net = client.networks.get(network_name)
    net.connect(container)
    logger.info("Connected %s to %s", container_name, network_name)
    return f"Connected {container_name} to {network_name}"


def _list_containers() -> list[dict]:
    client = get_docker()
    result = []
    for c in client.containers.list():
        nets = list(c.attrs["NetworkSettings"]["Networks"].keys())
        result.append({
            "name": c.name,
            "id": c.short_id,
            "image": c.image.tags[0] if c.image.tags else c.attrs["Config"]["Image"],
            "status": c.status,
            "networks": nets,
        })
    return sorted(result, key=lambda x: x["name"])


def _list_networks() -> list[dict]:
    client = get_docker()
    return sorted(
        [{"name": n.name, "id": n.short_id} for n in client.networks.list() if n.name not in ("none", "host", "bridge")],
        key=lambda x: x["name"],
    )


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    get_docker().ping()
    logger.info("Docker connected, Caddy Manager started")
    yield
    get_docker().close()


app = FastAPI(title="Caddy Manager", version="1.0.0", lifespan=lifespan)
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


# Global Docker error handler
@app.exception_handler(DockerException)
async def docker_error_handler(request: Request, exc: DockerException):
    return JSONResponse(status_code=502, content={"detail": f"Docker error: {exc}"})


# Mount MCP
from app.mcp_app import create_mcp_app
app.mount("/mcp", create_mcp_app())


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Web UI
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "base_domain": BASE_DOMAIN,
    })


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------
@app.get("/api/containers")
async def api_containers(_=Depends(verify_api_key)):
    return _list_containers()


@app.get("/api/networks")
async def api_networks(_=Depends(verify_api_key)):
    return _list_networks()


@app.get("/api/domains")
async def api_domains(_=Depends(verify_api_key)):
    return _parse_addon_domains()


@app.post("/api/domains")
async def api_create_domain(body: DomainCreate, _=Depends(verify_api_key)):
    existing = [d["subdomain"] for d in _parse_addon_domains()]
    if body.subdomain in existing:
        raise HTTPException(409, f"Domain {body.subdomain}.{BASE_DOMAIN} already exists")

    net_msg = ""
    if body.network:
        try:
            net_msg = _connect_container_to_network(body.container, body.network)
        except NotFound as e:
            raise HTTPException(404, str(e))
        except Exception as e:
            raise HTTPException(400, f"Network connection failed: {e}")

    filename = _write_domain_conf(body.subdomain, body.container, body.port)
    reload_msg = _reload_caddy()

    return {
        "status": "created",
        "file": filename,
        "domain": f"{body.subdomain}.{BASE_DOMAIN}",
        "network": net_msg,
        "reload": reload_msg,
    }


@app.delete("/api/domains/{subdomain}")
async def api_delete_domain(subdomain: str, _=Depends(verify_api_key)):
    if not _SUBDOMAIN_RE.match(subdomain):
        raise HTTPException(400, "Invalid subdomain format")
    try:
        filename = _delete_domain_conf(subdomain)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))

    reload_msg = _reload_caddy()
    return {"status": "deleted", "file": filename, "reload": reload_msg}


@app.post("/api/caddy/reload")
async def api_reload(_=Depends(verify_api_key)):
    return {"reload": _reload_caddy()}


@app.post("/api/networks/connect")
async def api_connect_network(container: str, network: str, _=Depends(verify_api_key)):
    try:
        msg = _connect_container_to_network(container, network)
    except NotFound as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(400, str(e))
    return {"status": msg}
