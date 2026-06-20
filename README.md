# lustre

Gay NSFW Discord auto-uploader bot. Pulls media from configurable Reddit subreddits (and optional local files) and posts on a schedule to an **age-restricted** channel.

## Setup

### 1. Create a Discord bot

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. **New Application** → **Bot** → **Reset Token** (copy it)
3. Enable **Message Content Intent** under Privileged Gateway Intents
4. **OAuth2 → URL Generator** → scopes: `bot` → permissions: `Send Messages`, `Embed Links`, `Attach Files`, `Read Message History`
5. Invite the bot to your server with that URL

**If you get `Missing Access (50001)`:**
- The bot is not in your server, or the channel ID is wrong
- Re-invite the bot using the OAuth URL above
- In channel settings → Permissions, make sure the bot's role can **View Channel**, **Send Messages**, **Embed Links**
- Copy the channel ID again (Developer Mode on → right-click channel → Copy Channel ID)

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env`:

| Variable | Description |
|---|---|
| `DISCORD_TOKEN` | Bot token from the developer portal |
| `CHANNEL_ID` | Target channel ID (must be **NSFW / age-restricted**) |
| `GUILD_ID` | Optional server ID — helps the bot find the channel |
| `SUBREDDITS` | Comma-separated list of gay NSFW subreddits |
| `POST_INTERVAL_MINUTES` | Minutes between posts (min 5) |
| `LOCAL_CONTENT_DIR` | Folder for your own images/videos (optional) |

### 3. Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python bot.py
```

Drop your own files into `content/` and they'll be mixed into the rotation.

## Deploy to Fly.io

Run the bot 24/7 on [Fly.io](https://fly.io). Stop your local `python bot.py` first so you don't double-post.

### 1. Install Fly CLI and log in

```bash
brew install flyctl   # or: curl -L https://fly.io/install.sh | sh
fly auth login
```

### 2. Create the app (first time only)

```bash
fly launch --no-deploy --copy-config --name lustre
```

Change `lustre` in `fly.toml` if that name is taken.

### 3. Create a volume (keeps dedup DB across restarts)

```bash
fly volumes create lustre_data --size 1 --region iad
```

Use the same region as `primary_region` in `fly.toml`.

### 4. Set secrets (don't commit these)

```bash
fly secrets set \
  DISCORD_TOKEN="your_token" \
  CHANNEL_ID="your_channel_id" \
  GUILD_ID="your_guild_id" \
  SUBREDDITS="gaybros,gayporn,men"
```

Optional: `POST_INTERVAL_MINUTES=30`

### 5. Deploy

```bash
fly deploy
```

### Useful commands

```bash
fly logs          # live logs
fly status        # machine status
fly ssh console   # shell into the container
fly apps restart lustre
```

Fly runs ~$0–5/mo on the free allowance for a small always-on machine. The volume adds a small monthly cost.

## Notes

- The target channel **must** be marked as age-restricted (NSFW) in Discord channel settings.
- Posted URLs are deduplicated in `data/posted.db` (or `$DATA_DIR/posted.db` on Fly) so you won't get repeats.
- Only use subreddits and local media you have the right to share. Respect Reddit and Discord ToS.
- For 24/7 uptime, deploy to Fly.io (see above) or use `pm2` / `systemd` locally.

## Commands

The bot runs fully automated — no slash commands needed. It starts posting once online.
