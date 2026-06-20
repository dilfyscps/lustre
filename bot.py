#!/usr/bin/env python3
"""Discord bot that auto-posts gay NSFW media to age-restricted channels."""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import ssl
from pathlib import Path

import certifi

# macOS Python often ships without CA certs; point SSL at certifi before any HTTPS.
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

import aiohttp
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
SUBREDDITS = [s.strip() for s in os.getenv("SUBREDDITS", "gaybros").split(",") if s.strip()]
POST_INTERVAL_MINUTES = max(5, int(os.getenv("POST_INTERVAL_MINUTES", "30")))
LOCAL_CONTENT_DIR = Path(os.getenv("LOCAL_CONTENT_DIR", "content"))
DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
DB_PATH = DATA_DIR / "posted.db"
REDDIT_UA = "lustre/1.0 (discord nsfw uploader bot)"
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

MEDIA_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".webm", ".mov"}
REDDIT_MEDIA_PATTERN = re.compile(
    r"^https?://("
    r"i\.redd\.it|"
    r"i\.imgur\.com|"
    r"v\.redd\.it|"
    r"redgifs\.com|"
    r"www\.redgifs\.com|"
    r"gfycat\.com|"
    r"media\.redgifs\.com"
    r")/",
    re.I,
)


class LustreBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.http_session: aiohttp.ClientSession | None = None
        self.db: aiosqlite.Connection | None = None

    async def setup_hook(self) -> None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(DB_PATH)
        await self.db.execute(
            "CREATE TABLE IF NOT EXISTS posted (url TEXT PRIMARY KEY, posted_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        await self.db.commit()

        connector = aiohttp.TCPConnector(ssl=SSL_CONTEXT)
        self.http_session = aiohttp.ClientSession(
            connector=connector,
            headers={"User-Agent": REDDIT_UA},
            timeout=aiohttp.ClientTimeout(total=30),
        )
        self.post_loop.start()
        # Don't wait 30 min for the first post.
        asyncio.create_task(self.do_post())

    async def close(self) -> None:
        self.post_loop.cancel()
        if self.http_session:
            await self.http_session.close()
        if self.db:
            await self.db.close()
        await super().close()

    async def already_posted(self, url: str) -> bool:
        assert self.db is not None
        async with self.db.execute("SELECT 1 FROM posted WHERE url = ?", (url,)) as cur:
            return await cur.fetchone() is not None

    async def mark_posted(self, url: str) -> None:
        assert self.db is not None
        await self.db.execute("INSERT OR IGNORE INTO posted (url) VALUES (?)", (url,))
        await self.db.commit()

    async def fetch_reddit_posts(self, subreddit: str) -> list[dict]:
        assert self.http_session is not None
        url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit=50&raw_json=1"
        async with self.http_session.get(url) as resp:
            if resp.status != 200:
                log.warning("Reddit returned %s for r/%s", resp.status, subreddit)
                return []
            data = await resp.json()

        posts: list[dict] = []
        for child in data.get("data", {}).get("children", []):
            post = child.get("data", {})
            if post.get("over_18") is not True:
                continue
            if post.get("stickied"):
                continue

            media_url = post.get("url_overridden_by_dest") or post.get("url", "")
            if not media_url or not REDDIT_MEDIA_PATTERN.match(media_url):
                preview = post.get("preview", {}).get("images", [])
                if preview:
                    media_url = preview[0].get("source", {}).get("url", "").replace("&amp;", "&")
                else:
                    continue

            posts.append(
                {
                    "url": media_url,
                    "title": post.get("title", "Untitled"),
                    "permalink": f"https://reddit.com{post.get('permalink', '')}",
                    "subreddit": subreddit,
                }
            )
        return posts

    def local_media_files(self) -> list[Path]:
        if not LOCAL_CONTENT_DIR.exists():
            return []
        return [
            p
            for p in LOCAL_CONTENT_DIR.iterdir()
            if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS
        ]

    async def pick_next_media(self) -> tuple[str, dict] | None:
        candidates: list[tuple[str, dict]] = []

        for subreddit in SUBREDDITS:
            posts = await self.fetch_reddit_posts(subreddit)
            random.shuffle(posts)
            for post in posts:
                if not await self.already_posted(post["url"]):
                    candidates.append(("reddit", post))

        local_files = self.local_media_files()
        random.shuffle(local_files)
        for path in local_files:
            key = f"local:{path.resolve()}"
            if not await self.already_posted(key):
                candidates.append(("local", {"path": path, "key": key}))

        if not candidates:
            return None
        return random.choice(candidates)

    async def post_to_channel(self, channel: discord.TextChannel, source: str, payload: dict) -> None:
        if not channel.is_nsfw():
            log.error("Channel #%s is not age-restricted. Enable NSFW on the channel.", channel.name)
            return

        if source == "reddit":
            url = payload["url"]
            embed = discord.Embed(
                title=payload["title"][:256],
                url=payload["permalink"],
                color=0xFF69B4,
            )
            embed.set_image(url=url)
            embed.set_footer(text=f"r/{payload['subreddit']} • lustre")
            await channel.send(content=url, embed=embed, suppress_embeds=False)
            await self.mark_posted(url)
            log.info("Posted reddit media: %s", url)
            return

        path: Path = payload["path"]
        file = discord.File(path, filename=path.name)
        await channel.send(content=f"🏳️‍🌈 **lustre**", file=file)
        await self.mark_posted(payload["key"])
        log.info("Posted local file: %s", path.name)

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s)", self.user, self.user.id if self.user else "?")
        if self.guilds:
            names = ", ".join(f"{g.name} ({g.id})" for g in self.guilds)
            log.info("In %d server(s): %s", len(self.guilds), names)
        else:
            log.error(
                "Bot is not in any servers. Invite it: Developer Portal → OAuth2 → URL Generator → "
                "scopes: bot, permissions: Send Messages + Embed Links + Attach Files"
            )

    async def get_target_channel(self) -> discord.TextChannel | None:
        if not CHANNEL_ID:
            log.error("CHANNEL_ID is not set in .env")
            return None

        channel = self.get_channel(CHANNEL_ID)
        if channel is not None and isinstance(channel, discord.TextChannel):
            return channel

        if GUILD_ID:
            guild = self.get_guild(GUILD_ID)
            if guild is None:
                log.error("GUILD_ID %s not found — is the bot invited to that server?", GUILD_ID)
            else:
                channel = guild.get_channel(CHANNEL_ID)
                if isinstance(channel, discord.TextChannel):
                    return channel

        for guild in self.guilds:
            channel = guild.get_channel(CHANNEL_ID)
            if isinstance(channel, discord.TextChannel):
                return channel

        try:
            channel = await self.fetch_channel(CHANNEL_ID)
        except discord.Forbidden:
            log.error(
                "Missing Access (50001): bot cannot see channel %s. Fix: "
                "1) Invite bot to the server, "
                "2) Give the bot role View Channel + Send Messages on that channel, "
                "3) Double-check CHANNEL_ID (right-click channel → Copy Channel ID)",
                CHANNEL_ID,
            )
            return None
        except discord.NotFound:
            log.error("Channel %s not found — wrong CHANNEL_ID?", CHANNEL_ID)
            return None

        if not isinstance(channel, discord.TextChannel):
            log.error("CHANNEL_ID must point to a text channel, not a voice/thread category")
            return None
        return channel

    async def do_post(self) -> None:
        await self.wait_until_ready()
        channel = await self.get_target_channel()
        if channel is None:
            return

        try:
            picked = await self.pick_next_media()
            if picked is None:
                log.info("No new media found. Add subreddits or drop files in %s/", LOCAL_CONTENT_DIR)
                return
            source, payload = picked
            await self.post_to_channel(channel, source, payload)
        except discord.HTTPException as exc:
            log.error("Discord API error: %s", exc)
        except Exception:
            log.exception("Unexpected error during post")

    @tasks.loop(minutes=POST_INTERVAL_MINUTES)
    async def post_loop(self) -> None:
        await self.do_post()

    @post_loop.before_loop
    async def before_post_loop(self) -> None:
        await self.wait_until_ready()


def validate_config() -> None:
    if not DISCORD_TOKEN:
        raise SystemExit("Set DISCORD_TOKEN in .env")
    if not CHANNEL_ID:
        raise SystemExit("Set CHANNEL_ID in .env (right-click channel → Copy Channel ID)")
    if not SUBREDDITS and not LOCAL_CONTENT_DIR.exists():
        raise SystemExit("Set SUBREDDITS and/or add files to content/")


def main() -> None:
    validate_config()
    LOCAL_CONTENT_DIR.mkdir(parents=True, exist_ok=True)
    bot = LustreBot()
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
