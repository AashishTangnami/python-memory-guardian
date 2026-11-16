# Local stand-in for the Linux jobs of .github/workflows/validate.yml: one CI environment per image
# (docker-compose.yml builds one per job). The image holds the toolchains and the pinned npm and pip
# dependencies; the code is bind-mounted at run time (scripts/ci/entrypoint.sh). /.dockerignore
# sends only the dependency manifests, so editing code never rebuilds an image.
#   PYTHON         official python image tag (3.9, 3.13)
#   FREE_THREADED  1: the image's CPython version is built again with --disable-gil and becomes `python`
#                  (a source build, as setup-python's 3.13t: the uv-managed build has no _testcapi, so
#                  runtime_acceptance_test.py reports a case incomplete). memray has no 3.13t wheel.
ARG PYTHON=3.13
FROM python:${PYTHON}-bookworm
# Latest release of the major, as setup-node's node-version: '22' (vsce needs 22.12+: require(esm)).
ARG NODE_MAJOR=22
ARG FREE_THREADED=
RUN set -eux; \
    case "$(dpkg --print-architecture)" in amd64) arch=x64 ;; arm64) arch=arm64 ;; *) exit 1 ;; esac; \
    base="https://nodejs.org/dist/latest-v${NODE_MAJOR}.x"; \
    line=$(curl -fsSL "$base/SHASUMS256.txt" | grep " node-v.*-linux-${arch}.tar.xz$"); \
    curl -fsSLo /tmp/node.tar.xz "$base/${line##* }"; \
    echo "${line%% *}  /tmp/node.tar.xz" | sha256sum -c -; \
    tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1; rm /tmp/node.tar.xz; \
    curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal --component clippy --default-toolchain stable; \
    if [ -n "$FREE_THREADED" ]; then \
      curl -fsSL "https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tar.xz" | tar -xJ -C /tmp; \
      cd "/tmp/Python-${PYTHON_VERSION}"; \
      ./configure --disable-gil --prefix=/opt/py --with-ensurepip=install > /dev/null; \
      make -j"$(nproc)" > /dev/null; make install > /dev/null; \
      ln -s "python${PYTHON_VERSION%.*}t" /opt/py/bin/python; cd /; rm -rf "/tmp/Python-${PYTHON_VERSION}"; \
      /opt/py/bin/python -c 'import sys, _testcapi; assert not sys._is_gil_enabled()'; \
    fi
ENV PATH=/opt/py/bin:/root/.cargo/bin:$PATH PMG_PYTHON=python
WORKDIR /work/src
COPY package.json package-lock.json requirements.txt requirements-acceptance.txt requirements-quality.txt ./
RUN set -eux; \
    npm ci --no-audit --no-fund; \
    python -m pip install -q --target src/python/libs -r requirements.txt; \
    if [ -n "$FREE_THREADED" ]; then grep -v '^memray' requirements-acceptance.txt > /tmp/acceptance.txt; \
    else cp requirements-acceptance.txt /tmp/acceptance.txt; fi; \
    python -m pip install -q -r /tmp/acceptance.txt -r requirements-quality.txt uv
COPY scripts/ci/entrypoint.sh /opt/ci/entrypoint.sh
ENTRYPOINT ["bash", "/opt/ci/entrypoint.sh"]
