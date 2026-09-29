package com.bmc.arsys.api;

public final class Field {
    private final int fieldId;
    private final String name;
    private final int dataType;
    private final FieldLimit fieldLimit;

    public Field(int fieldId, String name, int dataType) {
        this(fieldId, name, dataType, null);
    }

    public Field(
        int fieldId,
        String name,
        int dataType,
        FieldLimit fieldLimit
    ) {
        this.fieldId = fieldId;
        this.name = name;
        this.dataType = dataType;
        this.fieldLimit = fieldLimit;
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

    public FieldLimit getFieldLimit() {
        return fieldLimit;
    }
}
