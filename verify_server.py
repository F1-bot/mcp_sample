from server import mcp
import json

import asyncio

async def main():
    print("Verifying FastMCP Server...")

    # Check Tools
    print("\n--- Tools ---")
    try:
        tools = await mcp.get_tools()
        for name, tool in tools.items():
            print(f"Found tool: {name}")
    except Exception as e:
        print(f"Error listing tools: {e}")

    # Check Resources
    print("\n--- Resources ---")
    try:
        resources = await mcp.get_resources()
        for uri, resource in resources.items():
            print(f"Found resource: {uri}")
    except Exception as e:
        print(f"Error listing resources: {e}")

    # Check Prompts
    print("\n--- Prompts ---")
    try:
        prompts = await mcp.get_prompts()
        for name, prompt in prompts.items():
            print(f"Found prompt: {name}")
    except Exception as e:
        print(f"Error listing prompts: {e}")

    print("\nVerification Complete.")

if __name__ == "__main__":
    asyncio.run(main())


