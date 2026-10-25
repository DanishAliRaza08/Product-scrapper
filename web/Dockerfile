FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# Static export: any static host or CDN works; nginx here for the compose stack.
FROM nginx:1.27-alpine
COPY --from=build /app/out /usr/share/nginx/html
RUN printf 'server {\n listen 3000;\n root /usr/share/nginx/html;\n location / { try_files $uri $uri/ $uri/index.html =404; }\n add_header X-Content-Type-Options nosniff;\n add_header X-Frame-Options DENY;\n}\n' > /etc/nginx/conf.d/default.conf
EXPOSE 3000
