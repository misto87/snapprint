# Snapmaker Orca AppImages are built on Ubuntu 24.04 (glibc 2.39), so we use the same base.
FROM ubuntu:24.04

ARG ORCA_VERSION=2.4.0
ARG ORCA_TAG=v2.4.0
ARG ORCA_URL=https://github.com/Snapmaker/OrcaSlicer/releases/download/${ORCA_TAG}/Snapmaker_Orca_Linux_AppImage_Ubuntu2404_V${ORCA_VERSION}.AppImage
ARG SNAPPRINT_VERSION=dev

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    LIBGL_ALWAYS_SOFTWARE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl xvfb xauth \
        python3 python3-flask python3-waitress python3-requests \
        libgtk-3-0t64 libwebkit2gtk-4.1-0 libgl1 libglu1-mesa libegl1 libgl1-mesa-dri libosmesa6 \
        libgstreamer1.0-0 libgstreamer-plugins-base1.0-0 gstreamer1.0-plugins-base \
        libsecret-1-0 libnotify4 libsm6 libxkbcommon0 libdbus-1-3 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Download and unpack the official Snapmaker Orca AppImage (no FUSE needed at runtime).
RUN set -eux; \
    cd /tmp; \
    curl -fsSL -o orca.AppImage "${ORCA_URL}"; \
    chmod +x orca.AppImage; \
    ./orca.AppImage --appimage-extract >/dev/null; \
    mv squashfs-root /opt/orca; \
    rm orca.AppImage; \
    chmod -R a+rX /opt/orca; \
    test -x /opt/orca/AppRun

COPY tools /opt/snapprint/tools
# Take the official U1 machine/process/filament profiles from the unpacked AppImage.
RUN set -eux; \
    PROFILES="$(dirname "$(find /opt/orca -type d -path '*/profiles/Snapmaker' | head -n1)")"; \
    test -n "$PROFILES"; \
    python3 /opt/snapprint/tools/build_profiles.py "$PROFILES" /opt/snapprint/profiles; \
    echo "${ORCA_VERSION}" > /opt/snapprint/profiles/ORCA_VERSION

COPY app /opt/snapprint/app
COPY tests /opt/snapprint/tests

ENV SNAPPRINT_VERSION=${SNAPPRINT_VERSION} \
    SNAPPRINT_DATA_DIR=/data \
    SNAPPRINT_ORCA_BIN=/opt/orca/AppRun \
    HOME=/data/orca

WORKDIR /opt/snapprint
RUN mkdir -p /data && chown 1000:1000 /data
USER 1000:1000
EXPOSE 8080
VOLUME /data
CMD ["python3", "-m", "app.server"]
