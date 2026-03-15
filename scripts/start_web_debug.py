#!/usr/bin/env python3
"""
One-time debug script for Railway container filesystem inspection.
Run with: CMD ["python", "scripts/start_web_debug.py"]
Then switch back to scripts/start_web.py and remove this file or ignore.
"""
import os
import sys

print("===== DEBUG INFO =====")
print("Current working directory:", os.getcwd())
print("Script location:", __file__)
print("sys.path:", sys.path)
print("Directory listing of repo root ('.'):")
try:
    print(os.listdir("."))
except Exception as e:
    print("Error listing '.':", e)
print("Recursive listing:")
try:
    for root, dirs, files in os.walk(".", topdown=True):
        print(root, dirs, files)
except Exception as e:
    print("Error walking:", e)
print("Computed repo_root:", os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
print("===== END DEBUG =====")
