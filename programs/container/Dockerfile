# syntax=docker/dockerfile:1
#
# The operator container: one image that runs a Livepeer 2.0 testnet node
# from one command. It holds the unmodified node's orchestrator, built from
# the pinned commit, the light responder it serves, lp2-stake, lp2-explorer
# and the entrypoint that runs them (README.md beside this file).
#
# Build it from the repository's root:
#
#   docker build -t lp2-node -f programs/container/Dockerfile programs
#
# The node's source is fetched at NODE_COMMIT, the commit the deployment
# manifests name. --build-context node=<a checkout of that commit> builds
# from a copy already on the machine instead.

ARG GO_IMAGE=golang:1.27.4@sha256:205c7636d74e0ffbe3d007162df7d74f859f5a8c8322dfb9f907977686c126cf
ARG RUN_IMAGE=debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2

FROM ${GO_IMAGE} AS fetch
ARG NODE_SOURCE=https://github.com/livepeer/node
ARG NODE_COMMIT=e2b331958a5704016310153a7d33b7a634758f38
WORKDIR /src
RUN git init -q && git fetch -q --depth 1 "$NODE_SOURCE" "$NODE_COMMIT" && git checkout -q FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "$NODE_COMMIT"

# The node's source tree at its root, so that --build-context node=<dir>
# can stand in for it.
FROM scratch AS node
COPY --from=fetch /src /

FROM ${GO_IMAGE} AS build
ENV GOTOOLCHAIN=local GOFLAGS= GOWORK=off
COPY --from=node / /node
WORKDIR /node
RUN --mount=type=cache,target=/root/.cache/go-build --mount=type=cache,target=/go/pkg/mod \
    go build -o /out/livepeer-orchestrator ./cmd/livepeer-orchestrator
COPY . /programs
WORKDIR /programs
RUN --mount=type=cache,target=/root/.cache/go-build --mount=type=cache,target=/go/pkg/mod \
    go build -o /out/ ./cmd/lp2-stake ./cmd/lp2-responder ./cmd/lp2-explorer

FROM ${RUN_IMAGE}
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl jq \
    && rm -rf /var/lib/apt/lists/*
# The user the container runs as, and its data directory. A named volume
# mounted at /data starts owned by it; a host folder must be made writable
# for uid 10001, or the container run with --user (README.md).
RUN useradd --uid 10001 --user-group --home-dir /data --no-create-home --shell /usr/sbin/nologin lp2 \
    && mkdir /data && chown 10001:10001 /data && chmod 700 /data
COPY --from=build /out/ /usr/local/bin/
COPY --chmod=755 container/entrypoint /usr/local/bin/lp2-node
ENV HOME=/data
USER 10001:10001
WORKDIR /data
EXPOSE 8935 8080
ENTRYPOINT ["/usr/local/bin/lp2-node"]
