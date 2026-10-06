import aiosqlite
import os
from app.core.config import get_settings

settings = get_settings()


class Database:
    connection: aiosqlite.Connection = None

    async def connect(self):
        self.connection = await aiosqlite.connect(settings.DB_PATH)
        self.connection.row_factory = aiosqlite.Row
        await self._init_tables()

    async def disconnect(self):
        if self.connection:
            await self.connection.close()

    async def _init_tables(self):
        await self.connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        await self.connection.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                slug TEXT UNIQUE NOT NULL,
                date TEXT NOT NULL,
                secret_code TEXT,
                sync_status TEXT DEFAULT 'idle',
                last_sync_at TEXT,
                created_at TEXT NOT NULL
            )
        """)

        await self.connection.execute("""
            CREATE TABLE IF NOT EXISTS photos (
                id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                original_file_name TEXT,
                s3_object_key TEXT,
                thumbnail_s3_key TEXT,
                width INTEGER,
                height INTEGER,
                faces_count INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                error_detail TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (event_id) REFERENCES events (id)
            )
        """)

        await self.connection.execute("""
            CREATE TABLE IF NOT EXISTS faces (
                id TEXT PRIMARY KEY,
                photo_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                rekognition_face_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (photo_id) REFERENCES photos (id),
                FOREIGN KEY (event_id) REFERENCES events (id)
            )
        """)

        await self.connection.execute("""
            CREATE TABLE IF NOT EXISTS guests (
                id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT,
                selfie_path TEXT,
                status TEXT DEFAULT 'processing',
                match_count INTEGER DEFAULT 0,
                matched_photo_ids TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (event_id) REFERENCES events (id)
            )
        """)

        await self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_photos_event ON photos (event_id)")
        await self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_photos_s3_key ON photos (s3_object_key)")
        await self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_faces_event ON faces (event_id)")
        await self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_faces_rekognition ON faces (rekognition_face_id)")
        await self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_guests_event ON guests (event_id)")
        await self.connection.commit()

        # Safe migrations: add columns to existing databases.
        # SQLite raises OperationalError if the column already exists — silently ignore it.
        # Events created before payments existed default to 'active' / 'not_required'.
        for ddl in (
            "ALTER TABLE photos ADD COLUMN error_detail TEXT",
            "ALTER TABLE photos ADD COLUMN size_bytes INTEGER DEFAULT 0",
            "ALTER TABLE photos ADD COLUMN attempts INTEGER DEFAULT 0",
            "ALTER TABLE photos ADD COLUMN claimed_at TEXT",
            "ALTER TABLE photos ADD COLUMN next_attempt_at TEXT",
            "ALTER TABLE events ADD COLUMN owner_id TEXT",
            "ALTER TABLE events ADD COLUMN start_date TEXT",
            "ALTER TABLE events ADD COLUMN end_date TEXT",
            "ALTER TABLE events ADD COLUMN storage_capacity_gb REAL",
            "ALTER TABLE events ADD COLUMN status TEXT DEFAULT 'active'",
            "ALTER TABLE events ADD COLUMN amount_cents INTEGER DEFAULT 0",
            "ALTER TABLE events ADD COLUMN payment_status TEXT DEFAULT 'not_required'",
            "ALTER TABLE events ADD COLUMN payment_id TEXT",
            "ALTER TABLE events ADD COLUMN paid_at TEXT",
        ):
            try:
                await self.connection.execute(ddl)
                await self.connection.commit()
            except Exception:
                pass  # Column already exists — nothing to do

        await self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_owner ON events (owner_id)")
        await self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_photos_status ON photos (status)")
        await self.connection.commit()

    async def fetch_one(self, query, params=()):
        async with self.connection.execute(query, params) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def fetch_all(self, query, params=()):
        async with self.connection.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def execute(self, query, params=()):
        await self.connection.execute(query, params)
        await self.connection.commit()


db = Database()


async def connect_to_db():
    await db.connect()


async def close_db_connection():
    await db.disconnect()
