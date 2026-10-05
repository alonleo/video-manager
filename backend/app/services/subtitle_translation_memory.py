"""Bounded, optional local translation memory; cache failures never fail a job."""

import hashlib
import json
import sqlite3
import time
from pathlib import Path


class TranslationMemory:
    def __init__(self, path, namespace, limit=10000):
        self.db, self.limit = None, limit
        self.namespace = json.dumps(namespace, ensure_ascii=False, sort_keys=True)
        if path is None:
            return
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(str(path), timeout=2)
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS translations "
                "(key TEXT PRIMARY KEY, text TEXT NOT NULL, created REAL NOT NULL)"
            )
            self.db.commit()
        except (OSError, sqlite3.Error):
            self.close()

    def key(self, text):
        return hashlib.sha256((self.namespace + "\0" + text).encode()).hexdigest()

    def get(self, text):
        if self.db is None:
            return None
        try:
            row = self.db.execute(
                "SELECT text FROM translations WHERE key=?", (self.key(text),)
            ).fetchone()
            if row and isinstance(row[0], str) and row[0].strip() and len(row[0]) <= 4000:
                return row[0]
        except sqlite3.Error:
            self.close()
        return None

    def put_many(self, pairs):
        if self.db is None:
            return
        try:
            with self.db:
                self.db.executemany(
                    "INSERT OR REPLACE INTO translations VALUES (?, ?, ?)",
                    [(self.key(source), translated, time.time()) for source, translated in pairs],
                )
                # Cap across models and languages; interrupted jobs retain completed batches.
                self.db.execute(
                    "DELETE FROM translations WHERE key IN "
                    "(SELECT key FROM translations ORDER BY created DESC, key LIMIT -1 OFFSET ?)",
                    (self.limit,),
                )
        except sqlite3.Error:
            self.close()

    def close(self):
        if self.db is not None:
            try:
                self.db.close()
            except sqlite3.Error:
                pass
            self.db = None
