# Reference build environment for reproducible-build verification.
# The JDK tag below MUST match .java-version at the repo root
# (verify.sh enforces this). See docs/reproducible-builds.md for the
# toolchain upgrade procedure.
ARG JAVA_IMAGE=eclipse-temurin:17.0.19_10-jdk-noble@sha256:0386aaf49d6756b4856119f8e037f40cc865c7c8fbdda7c81733cc806f462daf
FROM ${JAVA_IMAGE}

ENV TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8
ENV ANDROID_HOME=/opt/android-sdk
ENV PATH=${ANDROID_HOME}/cmdline-tools/latest/bin:${ANDROID_HOME}/platform-tools:${ANDROID_HOME}/build-tools/35.0.0:${PATH}

RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl unzip ca-certificates python3-pip \
 && rm -rf /var/lib/apt/lists/*

RUN pip3 install --break-system-packages apksigcopier==1.1.1

ARG CMDLINE_TOOLS=11076708
RUN mkdir -p ${ANDROID_HOME}/cmdline-tools \
 && curl -fsSL -o /tmp/cmdline-tools.zip "https://dl.google.com/android/repository/commandlinetools-linux-${CMDLINE_TOOLS}_latest.zip" \
 && unzip -q /tmp/cmdline-tools.zip -d ${ANDROID_HOME}/cmdline-tools \
 && mv ${ANDROID_HOME}/cmdline-tools/cmdline-tools ${ANDROID_HOME}/cmdline-tools/latest \
 && rm /tmp/cmdline-tools.zip

RUN yes | sdkmanager --licenses > /dev/null \
 && sdkmanager "platform-tools" "platforms;android-35" "build-tools;35.0.0"

COPY verify-inner.sh /usr/local/bin/verify-inner.sh
RUN chmod +x /usr/local/bin/verify-inner.sh
WORKDIR /build
ENTRYPOINT ["/usr/local/bin/verify-inner.sh"]
