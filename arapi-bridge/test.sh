#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BUILD_DIR=$(mktemp -d "${TMPDIR:-/tmp}/helix-arapi-bridge-test.XXXXXX")
trap 'rm -rf "$BUILD_DIR"' EXIT HUP INT TERM

CLASS_DIR="$BUILD_DIR/classes"
SOURCE_LIST="$BUILD_DIR/sources.txt"
mkdir -p "$CLASS_DIR"

find "$SCRIPT_DIR/src/main/java" "$SCRIPT_DIR/src/test/java" \
    -type f -name '*.java' -print > "$SOURCE_LIST"

java --module jdk.compiler/com.sun.tools.javac.Main \
    -proc:none \
    -source 17 \
    -target 17 \
    -encoding UTF-8 \
    -Xlint:all,-options \
    -Werror \
    -d "$CLASS_DIR" \
    "@$SOURCE_LIST"

java -cp "$CLASS_DIR" com.example.helix.bridge.ArapiBridgeTest
