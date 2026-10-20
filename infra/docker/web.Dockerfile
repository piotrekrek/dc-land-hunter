# Build the static web app, then serve it with Caddy (which also proxies /api and /tiles).
FROM node:22.22-bookworm-slim AS build
RUN npm install -g pnpm@10.18.3
WORKDIR /app/web
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
ARG VITE_BASEMAP_STYLE
ENV VITE_BASEMAP_STYLE=${VITE_BASEMAP_STYLE}
ARG VITE_BASEMAP_STYLE_LIGHT
ENV VITE_BASEMAP_STYLE_LIGHT=${VITE_BASEMAP_STYLE_LIGHT}
RUN pnpm build

FROM caddy:2.11.7-alpine
COPY infra/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/web/dist /srv
