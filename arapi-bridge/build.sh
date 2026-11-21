#!/usr/bin/env sh
set -eu

if [ -z "${HELIX_ARAPI_JAR:-}" ]; then
    echo "HELIX_ARAPI_JAR is required" >&2
    exit 2
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BUILD_DIR="$SCRIPT_DIR/build"
CLASS_DIR="$BUILD_DIR/classes"
SOURCE_FILE="$SCRIPT_DIR/src/main/java/com/example/helix/bridge/ArapiBridge.java"

mkdir -p "$CLASS_DIR"
set -- "$SOURCE_FILE"
BUILD_CLASSPATH=$HELIX_ARAPI_JAR
if [ -n "${HELIX_CMDB_JAR:-}" ]; then
    BUILD_CLASSPATH="$BUILD_CLASSPATH:$HELIX_CMDB_JAR"
    set -- "$@" "$(dirname "$SOURCE_FILE")/CmdbSdk.java"
else
    rm -f "$CLASS_DIR/com/example/helix/bridge/CmdbSdk.class"
fi
java --module jdk.compiler/com.sun.tools.javac.Main \
    -proc:none \
    -encoding UTF-8 \
    -classpath "$BUILD_CLASSPATH" \
    -d "$CLASS_DIR" \
    "$@"
java --module jdk.jartool/sun.tools.jar.Main \
    --create \
    --file "$BUILD_DIR/helix-arapi-bridge.jar" \
    -C "$CLASS_DIR" .
