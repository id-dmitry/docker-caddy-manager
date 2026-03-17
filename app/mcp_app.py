"""MCP server for Caddy Manager — uses FastMCP with Streamable HTTP transport."""

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette


def create_mcp_app() -> Starlette:
    """Create MCP sub-app mounted at /mcp on the FastAPI app."""

    mcp = FastMCP(
        "caddy-manager",
        stateless_http=True,
        json_response=True,
    )

    @mcp.tool()
    def caddy_list_domains() -> list[dict]:
        """List all configured Caddy reverse proxy domain mappings."""
        from app.main import _parse_addon_domains
        return _parse_addon_domains()

    @mcp.tool()
    def caddy_create_domain(subdomain: str, container: str, port: int, network: str = "") -> dict:
        """Create a new subdomain -> container:port mapping. Writes site-*.conf and reloads Caddy.

        Args:
            subdomain: Subdomain label (e.g. 'myapp' for myapp.some-tools.org)
            container: Docker container name (e.g. 'myapp-web-1')
            port: Container port (e.g. 80)
            network: Docker network to connect container to (optional)
        """
        from app.main import (
            _parse_addon_domains, _write_domain_conf, _reload_caddy,
            _connect_container_to_network, BASE_DOMAIN,
        )

        existing = [d["subdomain"] for d in _parse_addon_domains()]
        if subdomain in existing:
            return {"error": f"Domain {subdomain}.{BASE_DOMAIN} already exists"}

        net_msg = ""
        if network:
            net_msg = _connect_container_to_network(container, network)

        filename = _write_domain_conf(subdomain, container, port)
        reload_msg = _reload_caddy()

        return {
            "status": "created",
            "domain": f"{subdomain}.{BASE_DOMAIN}",
            "file": filename,
            "network": net_msg,
            "reload": reload_msg,
        }

    @mcp.tool()
    def caddy_delete_domain(subdomain: str) -> dict:
        """Delete a subdomain mapping. Removes site-*.conf and reloads Caddy.

        Args:
            subdomain: Subdomain to delete (e.g. 'myapp')
        """
        from app.main import _delete_domain_conf, _reload_caddy
        try:
            filename = _delete_domain_conf(subdomain)
            reload_msg = _reload_caddy()
            return {"status": "deleted", "file": filename, "reload": reload_msg}
        except FileNotFoundError as e:
            return {"error": str(e)}

    @mcp.tool()
    def caddy_list_containers() -> list[dict]:
        """List all running Docker containers with names, images, and networks."""
        from app.main import _list_containers
        return _list_containers()

    @mcp.tool()
    def caddy_list_networks() -> list[dict]:
        """List all Docker networks."""
        from app.main import _list_networks
        return _list_networks()

    @mcp.tool()
    def caddy_connect_network(container: str, network: str) -> str:
        """Connect a Docker container to a network.

        Args:
            container: Container name
            network: Network name
        """
        from app.main import _connect_container_to_network
        return _connect_container_to_network(container, network)

    @mcp.tool()
    def caddy_reload() -> str:
        """Reload Caddy configuration."""
        from app.main import _reload_caddy
        return _reload_caddy()

    mcp.settings.streamable_http_path = "/"
    return mcp.streamable_http_app()
