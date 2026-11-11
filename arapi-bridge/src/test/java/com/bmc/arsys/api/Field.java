package com.bmc.arsys.api;

public final class Field {
    private final int fieldId;
    private final String name;
    private final int dataType;
    private final FieldLimit fieldLimit;
    private Value defaultValue;

    public int getFieldType() { return 1; }
    public int getFieldOption() { return 2; }
    public int getCreateMode() { return 1; }
    public int getSetFieldOptions() { return 0; }
    public int getAuditOption() { return 0; }
    public Value getDefaultValue() { return defaultValue; }
    public void setDefaultValue(Value value) { defaultValue = value; }
    public java.util.List<PermissionInfo> getPermissions() {
        return java.util.List.of(new PermissionInfo(0, 1), new PermissionInfo(1, 2));
    }

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
