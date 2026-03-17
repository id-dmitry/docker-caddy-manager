"""MCP SSE server for Caddy Manager — mounted at /mcp on the FastAPI app."""

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
from starlette.applications import Starlette
from starlette.routing import Route, Mount

import json


def create_mcp_app() -> Starlette:
    """Create a Starlette sub-app that serves MCP over SSE."""

    server = Server("caddy-manager")
    sse = SseServerTransport("/messages/")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="caddy_list_domains",
                description="List all configured domain mappings in Caddy addon directory",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            Tool(
                name="caddy_create_domain",
                description="Create a new subdomain -> container:port mapping. Writes site-*.conf and reloads Caddy.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "subdomain": {"type": "string", "description": "Subdomain name (without base domain), e.g. 'myapp'"},
                        "container": {"type": "string", "description": "Docker container name, e.g. 'myapp-web-1'"},
                        "port": {"type": "integer", "description": "Container port, e.g. 80"},
                        "network": {"type": "string", "description": "Docker network to connect the container to (optional, e.g. 'localai_default')", "default": ""},
                    },
                    "required": ["subdomain", "container", "port"],
                },
            ),
            Tool(
                name="caddy_delete_domain",
                description="Delete a subdomain mapping. Removes site-*.conf and reloads Caddy.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "subdomain": {"type": "string", "description": "Subdomain name to delete, e.g. 'myapp'"},
                    },
                    "required": ["subdomain"],
                },
            ),
            Tool(
                name="caddy_list_containers",
                description="List all running Docker containers with their names, images, and networks",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            Tool(
                name="caddy_list_networks",
                description="List all Docker networks",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
            Tool(
                name="caddy_connect_network",
                description="Connect a Docker container to a network",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "container": {"type": "string", "description": "Container name"},
                        "network": {"type": "string", "description": "Network name"},
                    },
                    "required": ["container", "network"],
                },
            ),
            Tool(
                name="caddy_reload",
                description="Reload Caddy configuration",
                inputSchema={"type": "object", "properties": {}, "required": []},
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        # Import service functions from main (avoid circular at module level)
        from app.main import (
            _parse_addon_domains,
            _write_domain_conf,
            _delete_domain_conf,
            _reload_caddy,
            _connect_container_to_network,
            _list_containers,
            _list_networks,
            BASE_DOMAIN,
        )

        try:
            if name == "caddy_list_domains":
                result = _parse_addon_domains()
                return [TextContent(type="text", text=json.dumps(result, indent=2))]

            elif name == "caddy_create_domain":
                subdomain = arguments["subdomain"]
                container = arguments["container"]
                port = arguments["port"]
                network = arguments.get("network", "")

                net_msg = ""
                if network:
                    net_msg = _connect_container_to_network(container, network)

                filename = _write_domain_conf(subdomain, container, port)
                reload_msg = _reload_caddy()

                result = {
                    "status": "created",
                    "domain": f"{subdomain}.{BASE_DOMAIN}",
                    "file": filename,
                    "network": net_msg,
                    "reload": reload_msg,
                }
                return [TextContent(type="text", text=json.dumps(result, indent=2))]

            elif name == "caddy_delete_domain":
                subdomain = arguments["subdomain"]
                filename = _delete_domain_conf(subdomain)
                reload_msg = _reload_caddy()
                result = {"status": "deleted", "file": filename, "reload": reload_msg}
                return [TextContent(type="text", text=json.dumps(result, indent=2))]

            elif name == "caddy_list_containers":
                result = _list_containers()
                return [TextContent(type="text", text=json.dumps(result, indent=2))]

            elif name == "caddy_list_networks":
                result = _list_networks()
                return [TextContent(type="text", text=json.dumps(result, indent=2))]

            elif name == "caddy_connect_network":
                msg = _connect_container_to_network(arguments["container"], arguments["network"])
                return [TextContent(type="text", text=msg)]

            elif name == "caddy_reload":
                msg = _reload_caddy()
                return [TextContent(type="text", text=msg)]

            else:
                return [TextContent(type="text", text=f"Unknown tool: {name}")]

        except Exception as e:
            return [TextContent(type="text", text=f"Error: {e}")]

    async def handle_sse(request):
        async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
            await server.run(streams[0], streams[1], server.create_initialization_options())

    return Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
        ],
    )
