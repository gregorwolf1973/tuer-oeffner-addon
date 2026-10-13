ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache python3 py3-pip

# cryptography (fuer die Passkeys) kommt als fertiges musllinux-Wheel -
# kein Rust-Build
# cbor2 ohne C-Teil: das Abbild hat keinen Compiler, die reine
# Python-Fassung reicht fuer ein paar Passkey-Anmeldungen locker.
COPY requirements.txt /tmp/requirements.txt
RUN CBOR2_BUILD_C_EXTENSION=0 pip3 install --no-cache-dir --break-system-packages --only-binary=cryptography -r /tmp/requirements.txt

COPY app /opt/tuer

# Syntaxfehler sollen den Build brechen, nicht erst den Start auf dem Pi
RUN python3 -m compileall -q /opt/tuer \
 && test -s /opt/tuer/static/icon-192.png

COPY run.sh /run.sh
RUN chmod a+x /run.sh

WORKDIR /opt/tuer
CMD [ "/run.sh" ]
