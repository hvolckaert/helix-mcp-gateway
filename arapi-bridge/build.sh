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
java --module jdk.compiler/com.sun.tools.javac.Main \
    -proc:none \
    -encoding UTF-8 \
    -classpath "$HELIX_ARAPI_JAR" \
    -d "$CLASS_DIR" \
    "$SOURCE_FILE"
java --module jdk.jartool/sun.tools.jar.Main \
    --create \
    --file "$BUILD_DIR/helix-arapi-bridge.jar" \
    -C "$CLASS_DIR" .
