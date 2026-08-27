# Reference build environment for reproducible-build verification.
# The JDK tag below MUST match .java-version at the repo root
# (verify.sh enforces this). See docs/reproducible-builds.md for the
# toolchain upgrade procedure.
ARG JAVA_IMAGE=eclipse-temurin:21.0.12_8-jdk-noble@sha256:1ca5e470ad60db0d5b4137c1357068fa210051d815ad98ea9964f4bbe7367f8c
FROM ${JAVA_IMAGE}

ENV TZ=UTC LANG=C.UTF-8 LC_ALL=C.UTF-8
ENV ANDROID_HOME=/opt/android-sdk
ENV PATH=${ANDROID_HOME}/cmdline-tools/latest/bin:${ANDROID_HOME}/platform-tools:${ANDROID_HOME}/build-tools/36.0.0:${PATH}

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
 && sdkmanager "platform-tools" "platforms;android-35" "build-tools;36.0.0"

COPY verify-inner.sh /usr/local/bin/verify-inner.sh
RUN chmod +x /usr/local/bin/verify-inner.sh
WORKDIR /build
ENTRYPOINT ["/usr/local/bin/verify-inner.sh"]
