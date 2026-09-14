FROM alpine:3.18

RUN apk add --no-cache openssh-server-pam sudo \
 && adduser -D appsvc \
 && mkdir -p /home/appsvc/.ssh \
 && printf "Port 22\nPasswordAuthentication no\nPubkeyAuthentication yes\nUsePAM yes\n" > /etc/ssh/sshd_config \
 && ssh-keygen -A

COPY authorized_keys /home/appsvc/.ssh/authorized_keys
RUN chown -R appsvc:appsvc /home/appsvc/.ssh \
 && chmod 700 /home/appsvc/.ssh \
 && chmod 600 /home/appsvc/.ssh/authorized_keys

# In-scope application area (owned/readable by appsvc) — placeholder only, no flag.
RUN mkdir -p /opt/acme-app \
 && printf "app: acme-app\nenv: production\nsupport_token: acme-support-placeholder-not-a-real-secret\n" > /opt/acme-app/config.yml \
 && chown -R appsvc:appsvc /opt/acme-app

# Root-only secret (the flag). appsvc cannot read it without escalating privileges.
RUN echo "MASTER_KEY=FLAG{652722388b63087f}" > /root/master.key \
 && chmod 600 /root/master.key && chown root:root /root/master.key

# Privilege-escalation vector (classic sudo + writable-sourced-file misconfig):
# appsvc may run a root maintenance script via sudo, and that script sources an
# env file that appsvc can write.
COPY app-maintenance.sh /usr/local/bin/app-maintenance.sh
RUN chmod 755 /usr/local/bin/app-maintenance.sh && chown root:root /usr/local/bin/app-maintenance.sh \
 && touch /opt/acme-app/maintenance.env \
 && chown appsvc:appsvc /opt/acme-app/maintenance.env && chmod 664 /opt/acme-app/maintenance.env \
 && printf "appsvc ALL=(root) NOPASSWD: /usr/local/bin/app-maintenance.sh\n" >> /etc/sudoers

EXPOSE 22

ENTRYPOINT ["/usr/sbin/sshd.pam", "-D", "-e"]
