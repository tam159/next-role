# NextRole frontend. Two targets share the dependency layer:
#   dev  (default, last stage) — `next dev` with the source bind-mounted by the
#        root docker-compose.yml; also runs one-off CLIs (Better Auth migrate).
#   prod — `next build` → standalone `node server.js`, used by
#        deploy/vultr/docker-compose.yml. NEXT_PUBLIC_* values are inlined at
#        build time, so they arrive as build args, not runtime env.

FROM node:24-alpine AS deps
WORKDIR /deps/next-role/frontend
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

# ---------- prod build ----------
FROM deps AS builder
COPY . .
ARG NEXT_PUBLIC_LANGGRAPH_DEPLOYMENT_URL
ARG NEXT_PUBLIC_LANGGRAPH_ASSISTANT_ID=career_agent
ARG NEXT_PUBLIC_LANGSMITH_API_KEY=""
ARG NEXT_PUBLIC_AUTH_ENABLED=false
ARG NEXT_PUBLIC_AUTH_GOOGLE_ENABLED=false
ARG NEXT_PUBLIC_AUTH_EMAIL_PASSWORD_ENABLED=true
ARG NEXT_PUBLIC_MODEL_OVERRIDE_ENABLED=true
ARG NEXT_PUBLIC_CONNECTION_SETTINGS_ENABLED=true
ENV NEXT_PUBLIC_LANGGRAPH_DEPLOYMENT_URL=$NEXT_PUBLIC_LANGGRAPH_DEPLOYMENT_URL \
    NEXT_PUBLIC_LANGGRAPH_ASSISTANT_ID=$NEXT_PUBLIC_LANGGRAPH_ASSISTANT_ID \
    NEXT_PUBLIC_LANGSMITH_API_KEY=$NEXT_PUBLIC_LANGSMITH_API_KEY \
    NEXT_PUBLIC_AUTH_ENABLED=$NEXT_PUBLIC_AUTH_ENABLED \
    NEXT_PUBLIC_AUTH_GOOGLE_ENABLED=$NEXT_PUBLIC_AUTH_GOOGLE_ENABLED \
    NEXT_PUBLIC_AUTH_EMAIL_PASSWORD_ENABLED=$NEXT_PUBLIC_AUTH_EMAIL_PASSWORD_ENABLED \
    NEXT_PUBLIC_MODEL_OVERRIDE_ENABLED=$NEXT_PUBLIC_MODEL_OVERRIDE_ENABLED \
    NEXT_PUBLIC_CONNECTION_SETTINGS_ENABLED=$NEXT_PUBLIC_CONNECTION_SETTINGS_ENABLED \
    NEXT_TELEMETRY_DISABLED=1
RUN pnpm build

FROM node:24-alpine AS prod
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
COPY --from=builder --chown=node:node /deps/next-role/frontend/.next/standalone ./
COPY --from=builder --chown=node:node /deps/next-role/frontend/.next/static ./.next/static
COPY --from=builder --chown=node:node /deps/next-role/frontend/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]

# ---------- local development (default target) ----------
FROM deps AS dev
COPY . .
EXPOSE 3000
CMD ["pnpm", "dev", "--hostname", "0.0.0.0"]
