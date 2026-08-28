package com.bmc.arsys.api;

public final class Timestamp {
    private final long value;

    public Timestamp(long value) {
        this.value = value;
    }

    public long getValue() {
        return value;
    }
}
