# Homelab fork

## Source of truth

- Fork: `bdekonin/Dispatcharr`.
- Custom branch: `homelab`, based on upstream `v0.31.0` (`bcbb68c4f054ee56383a41604cfcd7302b85da66`).
- Preserve the existing fixes before changing playback behavior or deploying a new image.
- The `star` stack is managed in Portainer, not in the private homelab repository. This fork owns the application code; Portainer owns the stack configuration.

## General session pinning

The movie-specific filter has been removed. First selection for a session writes `vod_source_pin:<session_id>` using Redis `SET NX`: concurrent workers use the winner's profile and exact upstream URL. HEAD probes and subsequent GET/Range requests with that session ID reuse it before considering provider priority or capacity. Existing persistent sessions seed a pin from their stored source.

Pin metadata expires after 24 hours without a request and is refreshed on use. It does not reserve a provider slot. The existing connection manager still handles atomic slot reservation, shared overlapping requests, and cleanup. Connection cleanup leaves the source pin available for a rewind/reconnect. If the pinned profile is disabled, disallowed, or Redis cannot supply a valid pin, that session fails rather than switching byte layouts; start a new playback session to select another provider.

An explicit session ID cannot adopt another viewer's idle session based on IP/user agent. Lazy episode-provider discovery remains unchanged and runs before the initial source is pinned.

Scope: pinning prevents switching provider URLs within a session; it cannot guarantee that a provider never replaces the bytes at the same URL. Direct Redirect-to-provider mode does not use the proxy's source pin.

## Validation and rollout

`.github/workflows/homelab.yml` runs all VOD regression tests in the pinned upstream Linux container on pushes/PRs to `homelab`, then builds `docker/Dockerfile.homelab` only after tests pass. No Python tests or dependency installation are required on the Mac. If GitHub Actions is disabled for the fork, enable its workflows in the repository's Actions tab.

Added regression coverage includes concurrent source claims, independent viewers, HEAD selection followed by a seek at full capacity, connection cleanup, legacy sessions, invalid pins, disabled profiles, Redis failure, and lazy episode failover. Existing slot-accounting tests now resume the same explicit session ID.

Status: implementation and CI configuration prepared; remote test results and production rollout must be verified before calling this validated. The local test attempt failed during dependency initialization and did not validate these changes. The currently deployed image remains `dispatcharr-homelab:0.31.0-617817fa`.

After green CI, build on the Docker host from the exact fork commit, update only Dispatcharr's image tag in Portainer, and keep re-pull disabled for a locally built tag. Preserve the data/VOD volumes, VPN configuration and Jellyfin settings. Restore any manually pinned `.strm` URL to its original sessionless Dispatcharr URL so each playback gets its own session; keep a backup of the workaround file. Do not leave a shared fixed session/profile in a library file.

Verify a fresh normal playback URL, HEAD and forward/backward 206 Range responses with the same total file size, and two independent viewers. Test seeking on the TV after the server checks. For rollback to the last deployed fork image, restore `dispatcharr-homelab:0.31.0-617817fa` in Portainer; no source bind mount is needed for that image.

## Verified inventory — 2026-10-11

The running Dispatcharr container uses a read-only bind mount:

```text
/opt/dispatcharr-patch/views.py:/app/apps/proxy/vod_proxy/views.py:ro
```

The host file and the file inside the running container have the same SHA-256:

```text
1c7c78b498cab32fee330af3853d7ba65ad8426b381dc01d7ecf5003cd1106ce
```

`views.py.orig` exactly matches this fork's upstream v0.31.0 file:

```text
915398069f95d0bfa6ac11e4125a9b3cb4485cbbc34e101cf1e955af0e0efec5
```

The host diff is `/tmp/dispatcharr-custom.diff` (143 lines including headers and context).
It contains:

1. Lazy episode-provider discovery when the existing episode candidates cannot supply a stream. Active alternative accounts are considered in priority order, available profile capacity is checked, and series episodes are fetched before one retry. A Redis lock limits duplicate fetches; the lock has a 120-second expiry and is removed afterward.
2. A temporary movie-specific source pin for the existing Limitless workaround. This is not a general playback-session implementation.

`docker diff` found no additional `.py`, `.sh`, `.conf`, `.js`, or `.tsx` changes under `/app` after excluding generated `/app/static` files. Bind mounts were inspected separately because Docker's writable-layer diff does not include them.

Existing host helper files include `patch_vod_failover.py`, `pin_limitless_temporary.py`, `download_limitless_local.py`, and `SEEK-WORKAROUND.md`. These are operational artifacts, not evidence of additional active application patches. Do not run them during the import.

The existing Portainer Compose also contains Jellyfin-specific configuration: a 4 GB tmpfs at `/cache/transcodes`, the Intel render device, and the render group. These settings were preserved. They belong to the deployment configuration, not Dispatcharr's source patch.

## Image build

- Build `docker/Dockerfile.homelab` with the repository root as its context. It uses the exact deployed upstream image digest and copies the custom source from this fork; rebuilding unchanged frontend assets is unnecessary for this increment.
- Validate the included code and playback, then replace the bind mount with the image's own code.
- Keep the current image and mount configuration available for rollback.

```sh
docker build -f docker/Dockerfile.homelab -t dispatcharr-homelab:0.31.0-<commit> .
```

This tag is local to the Docker host; no new registry credentials are required.

## Permanent seek behavior

Each playback session must keep the same provider, profile, and upstream file across overlapping Range requests and seeks. Requests within that session must reuse its capacity allocation; cleanup must release it reliably. A source with a different byte layout must not silently serve an existing session's byte ranges. Verify forward/backward seeks and independent simultaneous viewers before rollout.

## Deployment and validation — 2026-10-11

The exact custom source is committed as `617817fa`. The Docker host checkout is `/opt/dispatcharr-fork-617817fa`; Portainer's `star` stack now uses `dispatcharr-homelab:0.31.0-617817fa`. The `views.py` bind mount was removed; the data and VOD volumes remain attached.

Validation passed:

- Python syntax and exact source SHA-256 in the exported file, built image, and deployed container.
- Dispatcharr HTTP 200 after deployment; Gluetun and Jellyfin report healthy.
- Two real byte-range requests, at byte offsets 3,400,000,000 and 3,300,000,000: both returned HTTP 206, 1,024 bytes, and the same total file size (4,819,135,614 bytes).

This is a Range/rewind smoke test; it is not an end-to-end TV playback or multi-viewer validation. The general permanent seek behavior described above remains future work.

The temporary file-export SSH key was removed immediately after copying the file.

## Rollback

In Portainer's `star` stack, restore Dispatcharr's original pinned image:

```yaml
image: ghcr.io/dispatcharr/dispatcharr@sha256:f81924fa3dbfeb463b3908be7e086bf58aacbd2ba56062bfb555ee3a471acf8f
```

Restore its additional volume entry:

```yaml
- /opt/dispatcharr-patch/views.py:/app/apps/proxy/vod_proxy/views.py:ro
```

Deploy with re-pull disabled. The existing host patch and backups remain available; keep the data, VOD, and Jellyfin volume configuration intact.
