"""Browser Gateway 只读工具注册。"""

from .tools import register_browser_gateway_tools

register_browser_gateway_tools()

__all__ = ["register_browser_gateway_tools"]
