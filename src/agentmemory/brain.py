"""
brainctl — Python API for agent memory.

Quick start:
    from brainctl import Brain
    
    brain = Brain()                          # uses ~/brainctl/brain.db
    brain = Brain("/path/to/brain.db")       # custom path
    
    brain.remember("User prefers dark mode") # add a memory
    brain.search("preferences")              # search memories
    brain.entity("Chief", "person",          # create entity
        observations=["Founder", "Builder"])
    brain.log("Deployed v2.0")               # log an event
    brain.stats()                            # database stats
"""

import json
import os
import re
import sqlite3
from pathlib import Path

_INIT_SQL_PATH = Path(__file__).parent.parent.parent / "db" / "init_schema.sql"


class Brain:
    """Simple interface to brainctl's memory system."""
    
    def __init__(self, db_path=None, agent_id="default"):
        if db_path is None:
            db_path = os.environ.get("BRAIN_DB", str(Path.home() / "brainctl" / "brain.db"))
        self.db_path = Path(db_path)
        self.agent_id = agent_id
        
        if not self.db_path.exists():
            self._init_db()
    
    def _init_db(self):
        """Create a fresh brain.db with core schema."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path))
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL DEFAULT 'default',
                category TEXT NOT NULL DEFAULT 'general',
                scope TEXT NOT NULL DEFAULT 'global',
                content TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 1.0,
                tags TEXT,
                retired_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL DEFAULT 'default',
                event_type TEXT NOT NULL DEFAULT 'observation',
                summary TEXT NOT NULL,
                detail TEXT,
                project TEXT,
                importance REAL NOT NULL DEFAULT 0.5,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                properties TEXT NOT NULL DEFAULT '{}',
                observations TEXT NOT NULL DEFAULT '[]',
                agent_id TEXT NOT NULL DEFAULT 'default',
                confidence REAL NOT NULL DEFAULT 1.0,
                scope TEXT NOT NULL DEFAULT 'global',
                retired_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS knowledge_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_table TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                target_table TEXT NOT NULL,
                target_id INTEGER NOT NULL,
                relation_type TEXT NOT NULL,
                weight REAL NOT NULL DEFAULT 1.0,
                agent_id TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL DEFAULT 'default',
                title TEXT NOT NULL,
                rationale TEXT NOT NULL,
                project TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS memory_triggers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL DEFAULT 'default',
                trigger_condition TEXT NOT NULL,
                trigger_keywords TEXT NOT NULL,
                action TEXT NOT NULL,
                priority TEXT NOT NULL DEFAULT 'medium',
                status TEXT NOT NULL DEFAULT 'active',
                fired_at TEXT,
                expires_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
        """)
        conn.close()
    
    def _db(self):
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        return conn
    
    def remember(self, content, category="general", tags=None, confidence=1.0):
        """Add a memory. Returns memory ID."""
        db = self._db()
        tags_json = json.dumps(tags.split(",")) if isinstance(tags, str) else (json.dumps(tags) if tags else None)
        cur = db.execute(
            "INSERT INTO memories (agent_id, category, content, confidence, tags) VALUES (?,?,?,?,?)",
            (self.agent_id, category, content, confidence, tags_json)
        )
        db.commit()
        mid = cur.lastrowid
        db.close()
        return mid
    
    def search(self, query, limit=10):
        """Search memories by content. Returns list of dicts."""
        db = self._db()
        # Simple LIKE search (works without FTS5)
        rows = db.execute(
            "SELECT id, content, category, confidence, created_at FROM memories "
            "WHERE content LIKE ? AND retired_at IS NULL ORDER BY created_at DESC LIMIT ?",
            (f"%{query}%", limit)
        ).fetchall()
        results = [dict(r) for r in rows]
        db.close()
        return results
    
    def forget(self, memory_id):
        """Soft-delete a memory."""
        db = self._db()
        db.execute("UPDATE memories SET retired_at = datetime('now') WHERE id = ?", (memory_id,))
        db.commit()
        db.close()
    
    def log(self, summary, event_type="observation", project=None, importance=0.5):
        """Log an event. Returns event ID."""
        db = self._db()
        cur = db.execute(
            "INSERT INTO events (agent_id, event_type, summary, project, importance) VALUES (?,?,?,?,?)",
            (self.agent_id, event_type, summary, project, importance)
        )
        db.commit()
        eid = cur.lastrowid
        db.close()
        return eid
    
    def entity(self, name, entity_type, properties=None, observations=None):
        """Create or get an entity. Returns entity ID."""
        db = self._db()
        existing = db.execute(
            "SELECT id FROM entities WHERE name = ? AND retired_at IS NULL", (name,)
        ).fetchone()
        if existing:
            db.close()
            return existing["id"]
        
        props = json.dumps(properties) if properties else "{}"
        obs = json.dumps(observations) if observations else "[]"
        cur = db.execute(
            "INSERT INTO entities (name, entity_type, properties, observations, agent_id) VALUES (?,?,?,?,?)",
            (name, entity_type, props, obs, self.agent_id)
        )
        db.commit()
        eid = cur.lastrowid
        db.close()
        return eid
    
    def relate(self, from_entity, relation, to_entity):
        """Create a relation between two entities by name."""
        db = self._db()
        from_row = db.execute("SELECT id FROM entities WHERE name = ? AND retired_at IS NULL", (from_entity,)).fetchone()
        to_row = db.execute("SELECT id FROM entities WHERE name = ? AND retired_at IS NULL", (to_entity,)).fetchone()
        if not from_row or not to_row:
            db.close()
            raise ValueError(f"Entity not found: {from_entity if not from_row else to_entity}")
        db.execute(
            "INSERT OR IGNORE INTO knowledge_edges (source_table, source_id, target_table, target_id, relation_type, agent_id) "
            "VALUES ('entities', ?, 'entities', ?, ?, ?)",
            (from_row["id"], to_row["id"], relation, self.agent_id)
        )
        db.commit()
        db.close()
    
    def decide(self, title, rationale, project=None):
        """Record a decision."""
        db = self._db()
        cur = db.execute(
            "INSERT INTO decisions (agent_id, title, rationale, project) VALUES (?,?,?,?)",
            (self.agent_id, title, rationale, project)
        )
        db.commit()
        did = cur.lastrowid
        db.close()
        return did
    
    def stats(self):
        """Get database statistics."""
        db = self._db()
        stats = {}
        for tbl in ["memories", "events", "entities", "decisions", "knowledge_edges"]:
            try:
                stats[tbl] = db.execute(f"SELECT count(*) FROM {tbl}").fetchone()[0]
            except:
                stats[tbl] = 0
        try:
            stats["active_memories"] = db.execute(
                "SELECT count(*) FROM memories WHERE retired_at IS NULL"
            ).fetchone()[0]
        except:
            stats["active_memories"] = 0
        db.close()
        return stats
