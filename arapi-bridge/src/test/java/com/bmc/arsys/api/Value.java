package com.bmc.arsys.api;

public final class Value {
    private final Object value;
    private final DataType datatype;

    public Value(Object value) {
        this.value = value;
        this.datatype = value instanceof String ? DataType.CHAR
            : value instanceof Double ? DataType.REAL
            : value instanceof java.math.BigDecimal ? DataType.DECIMAL
            : DataType.INTEGER;
    }

    public Value(Object value, DataType datatype) {
        this.value = value;
        this.datatype = datatype;
    }

    public DataType getDataType() { return datatype; }

    public Object getValue() {
        return value;
    }
}
