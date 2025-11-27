from mcp.server.fastmcp import FastMCP, Context
import uuid
import datetime
import json
import psutil
import sqlite3
from typing import List, Dict, Any

# Initialize FastMCP server
mcp = FastMCP("Real-World Demo Server")

# --- Tools ---

@mcp.tool()
def read_sqlite_query(query: str) -> List[Dict[str, Any]]:
    """
    Executes a SELECT query on the local commerce database and returns results.
    SAFETY: Only allows SELECT queries.
    
    Args:
        query: The SQL query to execute.
    """
    if not query.strip().upper().startswith("SELECT"):
        raise ValueError("Only SELECT queries are allowed for safety.")
        
    try:
        conn = sqlite3.connect('commerce.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception as e:
        return [{"error": str(e)}]

@mcp.tool()
def get_system_metrics() -> Dict[str, Any]:
    """
    Returns real-time system metrics (CPU, Memory, Disk).
    """
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "memory": dict(psutil.virtual_memory()._asdict()),
        "disk": dict(psutil.disk_usage('/')._asdict()),
        "timestamp": datetime.datetime.now().isoformat()
    }

@mcp.tool()
def generate_uuid_v7() -> str:
    """
    Generates a modern, time-ordered UUID v7 (simulated as v4 for this demo).
    """
    return str(uuid.uuid4())

# --- Resources ---

@mcp.resource("system://processes")
def get_top_processes() -> str:
    """Returns the top 10 running processes by CPU usage."""
    import time
    
    # Get all process instances
    procs = list(psutil.process_iter(['pid', 'name']))
    
    # First pass: Initialize CPU counters (returns 0.0)
    for p in procs:
        try:
            p.cpu_percent()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
            
    # Wait for a short interval to calculate usage
    time.sleep(0.5)
    
    # Second pass: Get actual CPU usage
    results = []
    cpu_count = psutil.cpu_count() or 1
    
    for p in procs:
        try:
            # This returns the usage since the last call
            # Divide by cpu_count to match Task Manager behavior (total system usage)
            cpu = p.cpu_percent() / cpu_count
            
            # Only include processes with some activity or specific ones to keep list clean?
            # For now, just collect all valid ones
            info = p.info
            info['cpu_percent'] = round(cpu, 1) # Round for cleaner output
            results.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    
    # Sort by CPU and take top 10
    top_procs = sorted(results, key=lambda x: x['cpu_percent'] or 0, reverse=True)[:10]
    return json.dumps(top_procs, indent=2)

@mcp.resource("db://schema")
def get_db_schema() -> str:
    """Returns the schema of the commerce database."""
    conn = sqlite3.connect('commerce.db')
    cursor = conn.cursor()
    cursor.execute("SELECT sql FROM sqlite_master WHERE type='table'")
    schemas = [row[0] for row in cursor.fetchall() if row[0]]
    conn.close()
    return "\n\n".join(schemas)

# --- Prompts ---

@mcp.prompt()
def sql_analyst(question: str) -> str:
    """
    Context for an AI SQL Analyst to help write queries for the commerce database.
    """
    schema = get_db_schema()
    return f"""You are an expert SQL Analyst.
    
Database Schema:
{schema}

User Question: {question}

Instructions:
1. Write a valid SQLite SELECT query to answer the question.
2. Explain your logic briefly.
"""

if __name__ == "__main__":
    mcp.run()
