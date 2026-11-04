FROM public.ecr.aws/docker/library/nginx:stable-alpine@sha256:0e52bd054126cdc040b1276c0af347aeb02a41b9cfe305d6094767ac30e97949

COPY docker/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY LICENSE /usr/share/doc/wechat-assistant/LICENSE

EXPOSE 8080
