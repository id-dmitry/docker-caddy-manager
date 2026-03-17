# Docker Caddy Manager

Lightweight web UI + REST API + MCP server for managing Caddy reverse proxy domains in Docker environments.

## Features

- **Web UI** — manage domain → container mappings from browser
- **REST API** — programmatic domain management with API key auth
- **MCP Server** — SSE endpoint for Claude Code / AI agent integration
- **Docker-aware** — lists containers and networks, connects containers to networks
- **Zero-touch** — creates `site-*.conf` files and reloads Caddy automatically

## Quick Start

```bash
git clone https://github.com/odintsov/docker-caddy-manager.git
cd docker-caddy-manager
cp .env.example .env  # edit API_KEY
docker compose up -d --build
```

## Requirements

- Docker with Caddy running as a container
- Caddy config using `import /etc/caddy/addons/site-*.conf` pattern
- Docker socket accessible

## Configuration

| Variable | Default | Description |
|---|---|---|
| `API_KEY` | `caddy-mgr-secret-2026` | API authentication key |
| `ADDON_DIR` | `/data/addons` | Caddy addon configs mount path |
| `CADDY_CONTAINER` | `caddy` | Caddy container name |
| `BASE_DOMAIN` | `some-tools.org` | Base domain for subdomains |

## API

All endpoints require `X-API-Key` header or `Authorization: Bearer <key>`.

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/domains` | List configured domains |
| `POST` | `/api/domains` | Create domain mapping |
| `DELETE` | `/api/domains/{subdomain}` | Delete domain mapping |
| `GET` | `/api/containers` | List Docker containers |
| `GET` | `/api/networks` | List Docker networks |
| `POST` | `/api/networks/connect` | Connect container to network |
| `POST` | `/api/caddy/reload` | Reload Caddy config |

## MCP

SSE endpoint at `/mcp/sse` for AI agent integration.

Tools: `caddy_list_domains`, `caddy_create_domain`, `caddy_delete_domain`, `caddy_list_containers`, `caddy_list_networks`, `caddy_connect_network`, `caddy_reload`

## License

MIT
