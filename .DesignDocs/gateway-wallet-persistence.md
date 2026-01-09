# Gateway wallet persistence (recommended)

Why
---
- Encrypted wallet JSON files are sensitive and should not be checked into the repository.
- Developers running the local Gateway should have those wallet files persisted across container restarts when testing deeper functionality.
- Using a named Docker volume keeps wallet files off the working tree while still persisting them on the host.

What this change does
---------------------
- The project's `docker-compose.yml` and `gateway-src/docker-compose.yml` now mount a named Docker volume `gateway-wallets` to `/home/gateway/conf/wallets` inside the gateway container.
- The repository already ignores `gateway-files/` in `.gitignore`, so wallet files placed there won't be committed. The named volume keeps data even if the repo directory is empty.

How to use
----------
- Start the stack with the usual `docker compose up` (or your local equivalent). Docker will create the `gateway-wallets` volume automatically.

Migrate an existing wallet file into the named volume
---------------------------------------------------
If you already have an encrypted wallet JSON file (e.g. from a previous container run) and want to move it into the persistent volume, you can copy it into the volume as follows.

1) Create a temporary container that mounts the named volume and a host directory with the file, then copy the file into the volume.

```bash
# Replace <path/to/wallet.json> with the path to your wallet file on the host
docker run --rm \
  -v gateway-wallets:/target-volume \
  -v "$(pwd):/host" \
  alpine sh -c "cp /host/path/to/wallet.json /target-volume/"
```

2) Verify the file is inside the volume:

```bash
docker run --rm -v gateway-wallets:/data alpine ls -la /data
```

Alternative: mount a host directory
----------------------------------
If you prefer to keep wallet files in a specific host directory (for example, `~/.hummingbot/gateway/wallets`) instead of a Docker volume, update your local `docker-compose.yml` like this:

```yaml
services:
  hummingbot-gateway:
    volumes:
      - "/home/you/.hummingbot/gateway/wallets:/home/gateway/conf/wallets:rw"
      # keep other mounts for logs/certs/config as before
```

Security notes
--------------
- Never commit unencrypted private keys to the repository.
- The Docker volume is local to the host and not encrypted by Docker; treat the host machine as a trusted device.
- Use a strong `GATEWAY_PASSPHRASE` (your `.env` contains `GATEWAY_PASSPHRASE`) and store it securely.

Recommended PR notes
--------------------
- Explain that the compose change adds a named Docker volume `gateway-wallets` to persist gateway wallets outside the repo.
- Mention the migration steps above so other devs can recover wallets from previous local runs if needed.
- Remind reviewers that `gateway-files/` is in `.gitignore` so wallet JSON files won't be committed by accident.

If you'd like, I can also add a tiny helper script to copy an on-disk wallet into the volume automatically (or update `Makefile` targets). Tell me which you prefer.
