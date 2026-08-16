# RCPia web image. Node deps are installed with pnpm (no npm/yarn).
FROM node:22-bookworm-slim AS base
# Optional private/corporate npm registry. Used both for package installs and for
# the corepack download of pnpm itself.
ARG NPM_CONFIG_REGISTRY=https://registry.npmjs.org/
# Keep in sync with "packageManager" in package.json.
ARG PNPM_VERSION=10.33.4
ENV NPM_CONFIG_REGISTRY=${NPM_CONFIG_REGISTRY} \
    PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH
# npm is used only to bootstrap pnpm; all project dependencies install via pnpm.
# (corepack is not used: private registry mirrors often do not serve the
# version-manifest path corepack requires.)
RUN npm install -g pnpm@${PNPM_VERSION} && npm cache clean --force
WORKDIR /app

FROM base AS deps
COPY package.json pnpm-lock.yaml* ./
RUN --mount=type=cache,id=pnpm,target=/pnpm/store \
    pnpm install --frozen-lockfile

FROM base AS builder
ENV NEXT_TELEMETRY_DISABLED=1 \
    NEXT_OUTPUT=standalone
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN pnpm build

FROM base AS runner
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3000

CMD ["node", "server.js"]
