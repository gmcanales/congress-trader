FROM nginx:alpine

ARG GIT_HASH=dev

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html /usr/share/nginx/html/
COPY landing.html /usr/share/nginx/html/
COPY splash-admin.html /usr/share/nginx/html/
COPY docs.html /usr/share/nginx/html/
COPY honks.html /usr/share/nginx/html/
COPY friends.html /usr/share/nginx/html/
COPY games.html /usr/share/nginx/html/
COPY css /usr/share/nginx/html/css
COPY js /usr/share/nginx/html/js
COPY img /usr/share/nginx/html/img
COPY greetings.csv /usr/share/nginx/html/

# Inject cache-busting version into local JS/CSS asset URLs
RUN find /usr/share/nginx/html -name "*.html" | while read f; do \
      sed -i \
        -e "s|src=\"/js/\([^\"?]*\)\"|src=\"/js/\1?v=${GIT_HASH}\"|g" \
        -e "s|src=\"js/\([^\"?]*\)\"|src=\"js/\1?v=${GIT_HASH}\"|g" \
        -e "s|href=\"/css/\([^\"?]*\)\"|href=\"/css/\1?v=${GIT_HASH}\"|g" \
        -e "s|href=\"css/\([^\"?]*\)\"|href=\"css/\1?v=${GIT_HASH}\"|g" \
        "$f"; \
    done
