FROM node:24-alpine AS build

WORKDIR /app

COPY package*.json .

RUN npm install

COPY . .

# nuxt build dépasse le heap V8 par défaut sur une machine à 8 Go.
ENV NODE_OPTIONS=--max-old-space-size=4096

RUN npm run build

FROM node:24-alpine AS prod

WORKDIR /app

COPY --from=build /app/.output /app/.output

ENV NODE_ENV=production \
    TZ=Europe/Paris

EXPOSE 3000

CMD [ "node", ".output/server/index.mjs" ]
