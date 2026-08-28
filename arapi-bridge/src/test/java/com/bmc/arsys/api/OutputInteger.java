package com.bmc.arsys.api;

public final class OutputInteger {
    private long value;

    public void setValue(long value) {
        this.value = value;
    }

    public long longValue() {
        return value;
    }
}
