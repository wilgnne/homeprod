# 9Router

Deploys 9Router behind the repository's Traefik network at
`https://9router.lab.wilgnne.dev`.

## Deploy

```sh
cd 9router
cp .env.example .env
# Replace every `change-me` value in .env with a distinct, strong value.
docker compose up -d --build
```

The dashboard is available at `https://9router.lab.wilgnne.dev/dashboard` and
the OpenAI-compatible endpoint is `https://9router.lab.wilgnne.dev/v1`.

The `9router_data` named volume stores provider configuration, API keys and the
SQLite database. Do not remove it when recreating the container.

`REQUIRE_API_KEY=true` is enabled because this deployment is internet-facing;
create/copy an API key from the dashboard before using `/v1`.
