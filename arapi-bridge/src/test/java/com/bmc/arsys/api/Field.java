package com.bmc.arsys.api;

public final class Field {
    private final int fieldId;
    private final String name;
    private final int dataType;

    public Field(int fieldId, String name, int dataType) {
        this.fieldId = fieldId;
        this.name = name;
        this.dataType = dataType;
    }

    public int getFieldID() {
        return fieldId;
    }

    public String getName() {
        return name;
    }

    public int getDataType() {
        return dataType;
    }
}
