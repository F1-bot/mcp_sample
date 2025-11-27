from server import get_top_processes
import json

print("Calling get_top_processes()...")
result = get_top_processes()
print("Result:")
print(result)

data = json.loads(result)
print("\nParsed Data:")
for proc in data:
    print(f"PID: {proc.get('pid')}, Name: {proc.get('name')}, CPU: {proc.get('cpu_percent')}%")
