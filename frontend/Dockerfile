# syntax=docker/dockerfile:1.10
FROM oven/bun:1.2 AS deps

WORKDIR /app

# node-gyp toolchain — required because @metamask/sdk-communication-layer
# pulls utf-8-validate as a hard dep and it compiles native code on install.
# bun's base image has no npm/node-gyp; install full Node.js + node-gyp via apt.
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 make g++ nodejs npm \
 && npm install -g node-gyp \
 && rm -rf /var/lib/apt/lists/*

COPY package.json bun.lock ./

RUN --mount=type=cache,target=/root/.bun/install/cache \
    bun install --frozen-lockfile


FROM oven/bun:1.2 AS runner

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY --from=deps /app/node_modules ./node_modules
COPY . .

ENV NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=development \
    PORT=3000 \
    HOSTNAME=0.0.0.0

EXPOSE 3000

HEALTHCHECK --interval=5s --timeout=5s --retries=30 --start-period=45s \
  CMD curl -fsS -o /dev/null http://127.0.0.1:3000/ || exit 1

CMD ["bun", "run", "next", "dev", "--turbopack", "--port", "3000", "--hostname", "0.0.0.0"]
