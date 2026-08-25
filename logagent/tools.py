"""
Tool registry and execution plumbing for the agent harness.

A tool is defined by:
  - name: string
  - description: string (used by LLM to decide when to call the tool)
  - parameters: JSON Schema object for the input
  - dangerous: bool (default False) - if True, requires approval before execution
  - function: callable that takes a dict of arguments and returns a dict:
        {
            "content": List[Dict],  # list of content blocks (type:text, etc.)
            "is_error": bool,       # optional, default False
        }

The ToolRegistry maintains a mapping from name to tool definition.
"""

from typing import Dict, Any, List, Callable, Optional
import json


class ToolResult(Dict[str, Any]):
    """Type hint for tool result dict."""
    pass


class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, Dict[str, Any]] = {}

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        function: Callable[[Dict[str, Any]], ToolResult],
        dangerous: bool = False,
    ) -> None:
        """Register a new tool."""
        self._tools[name] = {
            "name": name,
            "description": description,
            "parameters": parameters,
            "function": function,
            "dangerous": dangerous,
        }

    def get_tool(self, name: str) -> Optional[Dict[str, Any]]:
        """Get tool definition by name."""
        return self._tools.get(name)

    def list_tools(self) -> List[Dict[str, Any]]:
        """List all registered tools (for LLM tool selection)."""
        # Return a list of tool specs without the function
        return [
            {
                "name": name,
                "description": tool["description"],
                "parameters": tool["parameters"],
                "dangerous": tool["dangerous"],
            }
            for name, tool in self._tools.items()
        ]

    def execute_tool(self, name: str, tool_input: Dict[str, Any]) -> ToolResult:
        """Execute a tool by name with given input."""
        tool = self._tools.get(name)
        if not tool:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Error: Tool '{name}' not found.",
                    }
                ],
                "is_error": True,
            }

        # Validate input against parameters? We'll skip for simplicity
        # but in a real implementation we'd use jsonschema

        try:
            result = tool["function"](tool_input)
            # Ensure result has the expected structure
            if not isinstance(result, dict):
                result = {"content": result}
            if "content" not in result:
                result["content"] = []
            if "is_error" not in result:
                result["is_error"] = False
            return result
        except Exception as e:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"Error executing tool '{name}': {str(e)}",
                    }
                ],
                "is_error": True,
            }