import os
import sqlite3
import json
from datetime import datetime
import threading

# Thread-safe connection local storage
class StateDB:
    def __init__(self, db_path="smc_engine_state.db"):
        self.db_path = db_path
        self._local = threading.local()
        self._init_db()

    def _get_conn(self):
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._local.conn.execute("PRAGMA journal_mode=WAL")
        return self._local.conn

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        # Key-Value store table (replaces active_setups.json, position_cache.json, etc.)
        c.execute('''
            CREATE TABLE IF NOT EXISTS kv_store (
                store_name TEXT,
                key TEXT,
                value TEXT,
                updated_at TIMESTAMP,
                PRIMARY KEY (store_name, key)
            )
        ''')
        
        # Fast access indices
        c.execute('CREATE INDEX IF NOT EXISTS idx_store_name ON kv_store(store_name)')
        
        conn.commit()
        conn.close()

    def set_value(self, store_name: str, key: str, value: dict | str | int | float | list):
        conn = self._get_conn()
        c = conn.cursor()
        val_str = json.dumps(value) if not isinstance(value, str) else value
        
        c.execute('''
            INSERT INTO kv_store (store_name, key, value, updated_at) 
            VALUES (?, ?, ?, ?)
            ON CONFLICT(store_name, key) DO UPDATE SET 
            value=excluded.value, updated_at=excluded.updated_at
        ''', (store_name, key, val_str, datetime.now()))
        conn.commit()

    def get_value(self, store_name: str, key: str, default=None):
        conn = self._get_conn()
        c = conn.cursor()
        c.execute('SELECT value FROM kv_store WHERE store_name=? AND key=?', (store_name, key))
        row = c.fetchone()
        
        if row:
            try:
                # Try to parse as JSON first (handles lists, dicts, int/float if saved as string)
                return json.loads(row[0])
            except json.JSONDecodeError:
                return row[0] # Return raw string if not JSON
        return default

    def get_all(self, store_name: str) -> dict:
        conn = self._get_conn()
        c = conn.cursor()
        c.execute('SELECT key, value FROM kv_store WHERE store_name=?', (store_name,))
        rows = c.fetchall()
        
        result = {}
        for k, v in rows:
            try:
                result[k] = json.loads(v)
            except json.JSONDecodeError:
                result[k] = v
        return result

    def delete_key(self, store_name: str, key: str):
        conn = self._get_conn()
        c = conn.cursor()
        c.execute('DELETE FROM kv_store WHERE store_name=? AND key=?', (store_name, key))
        conn.commit()

    def clear_store(self, store_name: str):
        conn = self._get_conn()
        c = conn.cursor()
        c.execute('DELETE FROM kv_store WHERE store_name=?', (store_name,))
        conn.commit()

# Expose a global default instance
db = StateDB()
