package com.bmc.arsys.api;

public final class Value {
    private final Object value;

    public Value(Object value) {
        this.value = value;
    }

    public Value(Object value, DataType datatype) {
        this.value = value;
    }

    public Object getValue() {
        return value;
    }
}
