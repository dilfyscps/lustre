# lustre

Discord bot that auto-posts media from a local folder to an age-restricted channel. No API keys needed.

## Setup

1. Create a Discord bot at [discord.com/developers](https://discord.com/developers/applications) and invite it to your server
2. Target channel must be **age-restricted (NSFW)**
3. Copy `.env.example` → `.env` and set `DISCORD_TOKEN` + `CHANNEL_ID`

## Usage

Drop images/videos into `content/`:

```
content/
  pic1.jpg
  vid1.mp4
  pic2.png
```

Supported: `.jpg` `.jpeg` `.png` `.gif` `.webp` `.mp4` `.webm` `.mov` (max 25MB per Discord)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python bot.py
```

- Posts one file on startup, then every `POST_INTERVAL_MINUTES` (default 30)
- Won't repost the same filename twice
- `CONTENT_LOOP=true` — starts over when all files are posted

## Fly.io

```bash
fly secrets set DISCORD_TOKEN="..." CHANNEL_ID="..." GUILD_ID="..." -a lustre
fly deploy -a lustre
```

Upload files to the server:

```bash
fly ssh sftp shell -a lustre
# then: cd /data/content, put your files
```

Or copy locally into `content/` before deploy won't work on Fly — use SFTP to `/data/content` on the volume.
