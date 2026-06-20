#!/usr/bin/env python3
"""Discord bot that auto-posts media from a local folder."""

from __future__ import annotations

import asyncio
import logging
import os
import random
from pathlib import Path

import certifi

os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

from aiohttp import web
import aiosqlite
import discord
from discord.ext import tasks
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("lustre")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
GUILD_ID = int(os.getenv("GUILD_ID", "0") or "0")
POST_INTERVAL_MINUTES = max(5, int(os.getenv("POST_INTERVAL_MINUTES", "30")))
LOCAL_CONTENT_DIR = Path(os.getenv("LOCAL_CONTENT_DIR", "content"))
CONTENT_LOOP = os.getenv("CONTENT_LOOP", "true").lower() in ("1", "true", "yes")
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
DB_PATH = DATA_DIR / "posted.db"
HEALTH_PORT = int(os.getenv("PORT", "8080"))
MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".webm", ".mov"}
_health_runner: web.AppRunner | None = None


def should_run_health_server() -> bool:
    return bool(os.getenv("FLY_APP_NAME"))


class LustreBot(discord.Client):
    def __init__(self) -> None:
        super().__init__(intents=discord.Intents.default())
        self.db: aiosqlite.Connection | None = None

    async def setup_hook(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        LOCAL_CONTENT_DIR.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(DB_PATH)
        await self._init_db()
        self.post_loop.start()

    async def _init_db(self) -> None:
        assert self.db is not None
        await self.db.execute(
            "CREATE TABLE IF NOT EXISTS posted (key TEXT PRIMARY KEY, posted_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        async with self.db.execute("PRAGMA table_info(posted)") as cur:
            columns = {row[1] for row in await cur.fetchall()}
        if "key" not in columns and "url" in columns:
            await self.db.execute("ALTER TABLE posted RENAME COLUMN url TO key")
        elif "key" not in columns:
            await self.db.execute("DROP TABLE posted")
            await self.db.execute(
                "CREATE TABLE IF NOT EXISTS posted (key TEXT PRIMARY KEY, posted_at TEXT DEFAULT CURRENT_TIMESTAMP)"
            )
        await self.db.commit()

    async def close(self) -> None:
        self.post_loop.cancel()
        if self.db:
            await self.db.close()
        await super().close()

    def local_media_files(self) -> list[Path]:
        if not LOCAL_CONTENT_DIR.exists():
            return []
        return sorted(
            p for p in LOCAL_CONTENT_DIR.iterdir()
            if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS
        )

    async def already_posted(self, key: str) -> bool:
        assert self.db is not None
        async with self.db.execute("SELECT 1 FROM posted WHERE key = ?", (key,)) as cur:
            return await cur.fetchone() is not None

    async def mark_posted(self, key: str) -> None:
        assert self.db is not None
        await self.db.execute("INSERT OR IGNORE INTO posted (key) VALUES (?)", (key,))
        await self.db.commit()

    async def reset_local_cycle(self) -> None:
        assert self.db is not None
        await self.db.execute("DELETE FROM posted WHERE key LIKE 'local:%'")
        await self.db.commit()
        log.info("All local files posted — starting over")

    async def pick_next_file(self) -> Path | None:
        files = self.local_media_files()
        if not files:
            return None

        candidates: list[Path] = []
        for path in files:
            key = f"local:{path.name}"
            if not await self.already_posted(key):
                candidates.append(path)

        if not candidates and CONTENT_LOOP:
            await self.reset_local_cycle()
            candidates = files

        if not candidates:
            return None
        return random.choice(candidates)

    async def get_target_channel(self) -> discord.TextChannel | None:
        if not CHANNEL_ID:
            log.error("CHANNEL_ID is not set")
            return None

        channel = self.get_channel(CHANNEL_ID)
        if isinstance(channel, discord.TextChannel):
            return channel

        if GUILD_ID:
            guild = self.get_guild(GUILD_ID)
            if guild:
                ch = guild.get_channel(CHANNEL_ID)
                if isinstance(ch, discord.TextChannel):
                    return ch

        for guild in self.guilds:
            ch = guild.get_channel(CHANNEL_ID)
            if isinstance(ch, discord.TextChannel):
                return ch

        try:
            channel = await self.fetch_channel(CHANNEL_ID)
        except discord.Forbidden:
            log.error("Missing Access to channel %s — check bot permissions", CHANNEL_ID)
            return None
        except discord.NotFound:
            log.error("Channel %s not found", CHANNEL_ID)
            return None

        return channel if isinstance(channel, discord.TextChannel) else None

    async def post_file(self, channel: discord.TextChannel, path: Path) -> None:
        if not channel.is_nsfw():
            log.error("Channel #%s is not age-restricted — enable NSFW", channel.name)
            return

        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb > 25:
            log.warning("Skipping %s — over Discord 25MB limit (%.1f MB)", path.name, size_mb)
            return

        await channel.send(file=discord.File(path, filename=path.name))
        await self.mark_posted(f"local:{path.name}")
        log.info("Posted %s", path.name)

    async def do_post(self) -> None:
        await self.wait_until_ready()
        channel = await self.get_target_channel()
        if channel is None:
            return

        try:
            path = await self.pick_next_file()
            if path is None:
                log.info("No media in %s/ — drop files there (.jpg .png .gif .mp4 etc.)", LOCAL_CONTENT_DIR)
                return
            await self.post_file(channel, path)
        except discord.HTTPException as exc:
            log.error("Discord API error: %s", exc)
        except Exception:
            log.exception("Unexpected error during post")

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s)", self.user, self.user.id if self.user else "?")
        count = len(self.local_media_files())
        log.info("Watching %s/ (%d files)", LOCAL_CONTENT_DIR, count)

    @tasks.loop(minutes=POST_INTERVAL_MINUTES)
    async def post_loop(self) -> None:
        await self.do_post()

    @post_loop.before_loop
    async def before_post_loop(self) -> None:
        await self.wait_until_ready()


async def health_handler(_request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def start_health_server() -> None:
    global _health_runner
    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)
    _health_runner = web.AppRunner(app)
    await _health_runner.setup()
    site = web.TCPSite(_health_runner, "0.0.0.0", HEALTH_PORT)
    await site.start()
    log.info("Health server listening on 0.0.0.0:%s", HEALTH_PORT)


async def stop_health_server() -> None:
    global _health_runner
    if _health_runner is not None:
        await _health_runner.cleanup()
        _health_runner = None


def validate_config() -> None:
    if not DISCORD_TOKEN:
        raise SystemExit("Set DISCORD_TOKEN")
    if not CHANNEL_ID:
        raise SystemExit("Set CHANNEL_ID")


async def run_bot() -> None:
    validate_config()
    LOCAL_CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    if should_run_health_server():
        await start_health_server()
    bot = LustreBot()
    try:
        async with bot:
            await bot.start(DISCORD_TOKEN)
    finally:
        await stop_health_server()


def main() -> None:
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
