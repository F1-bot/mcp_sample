# Real-World Demo Server

A powerful Model Context Protocol (MCP) server built with `FastMCP` that demonstrates real-world capabilities including system monitoring, SQLite database interaction, and UUID generation.

## Features

- **System Monitoring**: Real-time tracking of CPU, memory, and disk usage, plus process monitoring.
- **Database Integration**: Built-in SQLite database (`commerce.db`) simulating an e-commerce platform with products and orders.
- **Utilities**: Modern UUID generation.
- **AI Prompts**: Pre-configured prompts to help LLMs analyze data.

## Quick Start

### Prerequisites
- Python 3.10 or higher
- [uv](https://github.com/astral-sh/uv) (recommended)

### Installation

1. Install dependencies:
   ```bash
   uv add mcp psutil
   ```
   *Or using pip:*
   ```bash
   pip install mcp psutil
   ```

### Initialization

Initialize the sample database:
```bash
# Using uv
uv run setup_db.py

# Or using python directly
python setup_db.py
```

### Running the Server

Start the MCP server:
```bash
# Using uv
uv run server.py

# Or using python directly
python server.py
```

## Configuration

To use this server with Claude Desktop, add the following to your `claude_config.json`:

```json
{
  "mcpServers": {
    "RealWorldDemo": {
      "command": "uv",
      "args": [
        "--directory",
        "c:\\...",
        "run",
        "server.py"
      ]
    }
  }
}
```

## Tools & Schemas

### `read_sqlite_query`
Executes a SELECT query on the local commerce database.

- **Input Schema**:
  ```json
  {
    "type": "object",
    "properties": {
      "query": {
        "type": "string",
        "description": "The SQL SELECT query to execute"
      }
    },
    "required": ["query"]
  }
  ```

- **Output Schema**: `List[Dict[str, Any]]`
  ```json
  [
    {
      "column_name": "value",
      ...
    }
  ]
  ```

### `get_system_metrics`
Returns real-time system metrics.

- **Input Schema**: `{}` (No arguments)
- **Output Schema**:
  ```json
  {
    "cpu_percent": 12.5,
    "memory": {
      "total": 17179869184,
      "available": 8589934592,
      "percent": 50.0,
      "used": 8589934592,
      "free": 8589934592
    },
    "disk": {
      "total": 499963174912,
      "used": 200000000000,
      "free": 299963174912,
      "percent": 40.0
    },
    "timestamp": "2023-10-27T10:00:00.123456"
  }
  ```

### `generate_uuid_v7`
Generates a modern, time-ordered UUID.

- **Input Schema**: `{}` (No arguments)
- **Output Schema**: `String` (e.g., `"018b7796-6b2a-7c98-8e3f-123456789abc"`)

## Resources

### `system://processes`
Returns the top 10 running processes by CPU usage.

- **URI**: `system://processes`
- **MIME Type**: `application/json`
- **Schema**:
  ```json
  [
    {
      "pid": 1234,
      "name": "python.exe",
      "cpu_percent": 5.2,
      ...
    }
  ]
  ```

### `db://schema`
Returns the schema of the commerce database.

- **URI**: `db://schema`
- **MIME Type**: `text/plain`
- **Content**: SQL `CREATE TABLE` statements.

## Prompts

### `sql_analyst`
Context for an AI SQL Analyst to help write queries for the commerce database.
- **Arguments**:
    - `question`: The user's question about the data.
- **Usage**: Use this prompt when you want to ask questions like "What are the top selling products?" or "Show me recent orders."

## Database Schema

The server uses a SQLite database `commerce.db` with the following structure:

### `products`
| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Primary Key |
| `name` | TEXT | Product Name |
| `category` | TEXT | Product Category |
| `price` | REAL | Price |
| `stock` | INTEGER | Quantity in stock |

### `orders`
| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Primary Key |
| `product_id` | INTEGER | Foreign Key -> `products.id` |
| `quantity` | INTEGER | Quantity ordered |
| `order_date` | TEXT | Date of order |
| `status` | TEXT | Order status (pending, shipped, etc.) |
