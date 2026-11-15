# --- dev: Vite dev server with hot reload (docker-compose.dev.yml targets this) ---
FROM node:22-alpine AS dev
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
CMD ["npm", "run", "dev", "--", "--host"]

# --- build: compile the static bundle ---
FROM dev AS build
# Shown in the menu: the release's version for published images (set by the
# release build); empty when built from source
ARG APP_VERSION=
ENV APP_VERSION=$APP_VERSION
# The menu's "Source code" link; empty means the project's own repository
ARG SOURCE_URL=
ENV SOURCE_URL=$SOURCE_URL
RUN npm run build

# --- prod (default): nginx serving the built files, no dev server or source ---
FROM nginx:stable-alpine AS prod
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
