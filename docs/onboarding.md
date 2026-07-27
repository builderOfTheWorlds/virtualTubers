# Onboarding

## Getting your Twitch stream key

This project streams over RTMP, so the credential you need is your Twitch **stream key**, not a developer API key. It's used to set one of the indexed `WORKER_1_STREAM_KEY`..`WORKER_8_STREAM_KEY` variables — one per generic worker (see [README.md](../README.md#installation) and [docs/blank_workers.md](blank_workers.md)).

1. Go to [dashboard.twitch.tv](https://dashboard.twitch.tv) (or click your avatar → **Creator Dashboard**)
2. In the left sidebar, click the **Settings** gear icon → **Stream**
3. Under **Primary Stream key**, click **Show key**, then **Copy**

Keep it secret — treat it like a password. If it ever leaks, you can reset it from the same page.

### Note: this is different from the Twitch developer API

If you need Helix API access (e.g. chat/bot integration) rather than RTMP streaming, that's a separate flow and isn't currently used anywhere in this codebase:

1. Go to [dev.twitch.tv/console](https://dev.twitch.tv/console)
2. **Register Your Application**
3. This gives you a **Client ID** and **Client Secret**
