FROM nginx:alpine

ARG GIT_HASH=

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html /usr/share/nginx/html/
COPY landing.html /usr/share/nginx/html/
COPY splash-admin.html /usr/share/nginx/html/
COPY docs.html /usr/share/nginx/html/
COPY honks.html /usr/share/nginx/html/
COPY friends.html /usr/share/nginx/html/
COPY pond-puzzles.html /usr/share/nginx/html/
COPY crosswords.html /usr/share/nginx/html/
COPY games.html /usr/share/nginx/html/
COPY bsg-trail.html /usr/share/nginx/html/
COPY spacex.html /usr/share/nginx/html/
COPY spacex-mailer.html /usr/share/nginx/html/
COPY spacex-accountability.html /usr/share/nginx/html/
COPY css /usr/share/nginx/html/css
COPY js /usr/share/nginx/html/js
COPY img /usr/share/nginx/html/img
COPY music /usr/share/nginx/html/music
COPY data /usr/share/nginx/html/data
COPY greetings.csv /usr/share/nginx/html/

# Inject cache-busting version into local JS/CSS asset URLs.
# nginx serves these with Cache-Control: immutable (1y), so the URL
# MUST change every build or the browser will serve permanently stale
# files. Falls back to a build timestamp if GIT_HASH wasn't passed in
# (i.e. when called as `docker compose up --build` directly, without
# running scripts/deploy.sh). The deploy script still passes the git
# short-hash so same-commit redeploys reuse the same URL.
RUN HASH="${GIT_HASH:-dev-$(date +%s)}" && \
  find /usr/share/nginx/html -name "*.html" | while read f; do \
  sed -i \
  -e "s|src=\"/js/\([^\"?]*\)\"|src=\"/js/\1?v=${HASH}\"|g" \
  -e "s|src=\"js/\([^\"?]*\)\"|src=\"js/\1?v=${HASH}\"|g" \
  -e "s|href=\"/css/\([^\"?]*\)\"|href=\"/css/\1?v=${HASH}\"|g" \
  -e "s|href=\"css/\([^\"?]*\)\"|href=\"css/\1?v=${HASH}\"|g" \
  "$f"; \
  done
