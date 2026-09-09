# syntax=docker/dockerfile:1.7
#
# Builds a statically linked LCModel executable for the target architecture.
#
# The image serves two purposes. CI extracts /usr/local/bin/lcmodel with "docker create"
# + "docker cp" and publishes it as a plain compressed binary - static linking is what
# makes the extracted file run on any Linux of that architecture with no libgfortran or
# libc to match. The same image is also pushed to ghcr.io/julianmer/lcmodel, where
# PyLCModel's container fallback (lcmodel_wrapper/container.py) runs it directly:
#   docker run --rm -i -v "$PWD:$PWD" -w "$PWD" ghcr.io/julianmer/lcmodel < control.file
#
# LCModel is a separate BSD-3-Clause program by Stephen Provencher. It is fetched from
# the community source mirror at a pinned commit and compiled unmodified.
# See /licenses inside the image, and LICENSE.lcmodel in this repository.
#
# Build:
#   docker build -f docker/Dockerfile --build-arg LCMODEL_REF=<sha> -t lcmodel-build .
# Extract:
#   cid=$(docker create lcmodel-build) \
#     && docker cp "$cid:/usr/local/bin/lcmodel" ./lcmodel && docker rm "$cid"

ARG ALPINE_VERSION=3.20

#--------------------------------------------------------------------------------------#
#  stage 1: fetch the pinned, checksum-verified upstream source                          #
#--------------------------------------------------------------------------------------#
FROM alpine:${ALPINE_VERSION} AS src

# Pin to a commit, never a branch: "main" moving would silently change the numerics.
ARG LCMODEL_REF=c9d95ff9b1fd2e7e8b256088ba1f5cf4df5fb207
ARG LCMODEL_RAW=https://raw.githubusercontent.com/schorschinho/LCModel

RUN apk add --no-cache curl
WORKDIR /src

# LCModel.f INCLUDEs the other six; compiling it alone fails at its first INCLUDE.
RUN for f in LCModel.f lcmodel.inc lipid-1.inc liver-1.inc muscle-1.inc \
             nml_lcmodel.inc nml_lcmodl.inc; do \
        curl -fsSL --retry 3 --retry-delay 2 \
             -o "$f" "${LCMODEL_RAW}/${LCMODEL_REF}/source/$f" || exit 1; \
    done

COPY docker/lcmodel-src.sha256 ./SHA256SUMS
RUN sha256sum -c SHA256SUMS


#--------------------------------------------------------------------------------------#
#  stage 2: compile                                                                      #
#--------------------------------------------------------------------------------------#
FROM alpine:${ALPINE_VERSION} AS build

RUN apk add --no-cache gfortran musl-dev binutils
WORKDIR /build
COPY --from=src /src/ ./

# Flags taken verbatim from the upstream Makefile and README so the result matches the
# community builds. No -march/-mtune: a CI runner with wider vector support than the
# user's machine would emit a binary that dies with SIGILL.
RUN gfortran -c -fno-backslash -fno-f2c -O3 -fall-intrinsics -std=legacy \
             -Wuninitialized -ffpe-summary=none LCModel.f -o LCModel.o \
 && gfortran -static LCModel.o -o lcmodel \
 && strip lcmodel

# Fail the build here rather than at a user's site.
# LCModel exits 0 even on fatal errors (Fortran STOP), so assert on its output, not $?.
RUN ./lcmodel < /dev/null 2>&1 | grep -q 'MYCONT' \
 && ! ldd lcmodel 2>&1 | grep -q '=>' \
 && echo "self-test ok: runs, and is statically linked"

RUN mkdir -p /rootfs/usr/local/bin /rootfs/licenses /rootfs/work /rootfs/tmp \
 && cp lcmodel /rootfs/usr/local/bin/lcmodel \
 && chmod 1777 /rootfs/tmp


#--------------------------------------------------------------------------------------#
#  stage 3: minimal carrier image                                                        #
#--------------------------------------------------------------------------------------#
FROM scratch

ARG LCMODEL_REF=c9d95ff9b1fd2e7e8b256088ba1f5cf4df5fb207

COPY --from=build /rootfs/ /
COPY --from=src   /src/    /usr/src/lcmodel/
COPY LICENSE.lcmodel NOTICE /licenses/

# LCModel opens Fortran scratch files ("open(..., status='scratch')") before it does
# anything else, and libgfortran resolves those via GFORTRAN_TMPDIR -> TMPDIR -> /tmp.
# On a scratch image with no writable /tmp the very first thing it does fails.
ENV GFORTRAN_TMPDIR=/tmp

LABEL org.opencontainers.image.title="LCModel" \
      org.opencontainers.image.description="LCModel by Stephen Provencher, compiled unmodified from the community source mirror. Not affiliated with or endorsed by the copyright holder." \
      org.opencontainers.image.licenses="BSD-3-Clause" \
      org.opencontainers.image.source="https://github.com/julianmer/PyLCModel" \
      org.opencontainers.image.url="https://s-provencher.com/lcmodel.shtml" \
      org.pylcmodel.lcmodel.upstream.ref="${LCMODEL_REF}"

WORKDIR /work
ENTRYPOINT ["/usr/local/bin/lcmodel"]
