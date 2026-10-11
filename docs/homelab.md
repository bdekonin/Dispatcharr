# Homelab fork

## Source of truth

- Fork: `bdekonin/Dispatcharr`.
- Custom branch: `homelab`, based on upstream `v0.31.0` (`bcbb68c4f054ee56383a41604cfcd7302b85da66`).
- Preserve the existing fixes before changing playback behavior or deploying a new image.

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

## Next increment

- Build `docker/Dockerfile.homelab` with the repository root as its context. It uses the exact deployed upstream image digest and copies the custom source from this fork; rebuilding unchanged frontend assets is unnecessary for this increment.
- Validate the included code and playback, then replace the bind mount with the image's own code.
- Keep the current image and mount configuration available for rollback.

```sh
docker build -f docker/Dockerfile.homelab -t dispatcharr-homelab:0.31.0-<commit> .
```

This tag is local to the Docker host; no new registry credentials are required.

## Permanent seek behavior

Each playback session must keep the same provider, profile, and upstream file across overlapping Range requests and seeks. Requests within that session must reuse its capacity allocation; cleanup must release it reliably. A source with a different byte layout must not silently serve an existing session's byte ranges. Verify forward/backward seeks and independent simultaneous viewers before rollout.

## Current status

Fork and baseline branch are published. The exact custom source has been imported and its syntax and SHA-256 verified. The temporary file-export SSH key was removed immediately after copying the file. The custom image build and deployment are the remaining steps.
