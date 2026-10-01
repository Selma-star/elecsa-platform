# Stage 1: build the React app into static files
FROM node:20-slim AS build
WORKDIR /app
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# Stage 2: Caddy serves those static files AND reverse-proxies /api to
# the backend, with automatic HTTPS (Let's Encrypt) baked in - one
# container does the job a separate nginx + certbot setup usually needs.
FROM caddy:2-alpine
COPY --from=build /app/dist /srv
COPY Caddyfile /etc/caddy/Caddyfile
