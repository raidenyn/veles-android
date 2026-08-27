# Reference build environment for reproducible-build verification.
# The JDK tag below MUST match .java-version at the repo root
# (verify.sh enforces this). See docs/reproducible-builds.md for the
# toolchain upgrade procedure.
ARG JAVA_IMAGE=eclipse-temurin:21.0.12_8-jdk-noble@sha256:1ca5e470ad60db0d5b4137c1357068fa210051d815ad98ea9964f4bbe7367f8c
FROM ${JAVA_IMAGE}

ENV TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8
ENV ANDROID_HOME=/opt/android-sdk

RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl unzip ca-certificates python3-pip build-essential \
 && rm -rf /var/lib/apt/lists/*

RUN pip3 install --break-system-packages apksigcopier==1.1.1

ARG CMDLINE_TOOLS=11076708
RUN mkdir -p ${ANDROID_HOME}/cmdline-tools \
 && curl -fsSL -o /tmp/cmdline-tools.zip "https://dl.google.com/android/repository/commandlinetools-linux-${CMDLINE_TOOLS}_latest.zip" \
 && unzip -q /tmp/cmdline-tools.zip -d ${ANDROID_HOME}/cmdline-tools \
 && mv ${ANDROID_HOME}/cmdline-tools/cmdline-tools ${ANDROID_HOME}/cmdline-tools/latest \
 && rm /tmp/cmdline-tools.zip

ENV RUSTUP_HOME=/opt/rustup
ENV CARGO_HOME=/opt/cargo
ENV PATH=${CARGO_HOME}/bin:${ANDROID_HOME}/cmdline-tools/latest/bin:${ANDROID_HOME}/platform-tools:${ANDROID_HOME}/build-tools/36.0.0:${PATH}

ARG RUSTUP_VERSION=1.29.0
ARG RUSTUP_SHA256=4acc9acc76d5079515b46346a485974457b5a79893cfb01112423c89aeb5aa10
RUN curl -fsSL -o /tmp/rustup-init \
      "https://static.rust-lang.org/rustup/archive/${RUSTUP_VERSION}/x86_64-unknown-linux-gnu/rustup-init" \
 && echo "${RUSTUP_SHA256}  /tmp/rustup-init" | sha256sum -c - \
 && chmod +x /tmp/rustup-init \
 && /tmp/rustup-init -y --no-modify-path --profile minimal --default-toolchain none \
 && rm /tmp/rustup-init

COPY rust/rust-toolchain.toml /tmp/veles-rust/rust-toolchain.toml
RUN cd /tmp/veles-rust \
 && rustup show active-toolchain \
 && rustc --version \
 && rm -rf /tmp/veles-rust

ARG NDK_VERSION=29.0.14206865
RUN yes | sdkmanager --licenses > /dev/null \
 && sdkmanager \
      "platform-tools" \
      "platforms;android-35" \
      "build-tools;36.0.0" \
      "ndk;${NDK_VERSION}"

COPY verify/verify-inner.sh /usr/local/bin/verify-inner.sh
RUN chmod +x /usr/local/bin/verify-inner.sh
WORKDIR /build
ENTRYPOINT ["/usr/local/bin/verify-inner.sh"]
