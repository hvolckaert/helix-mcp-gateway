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
    public DisplayInstanceMap getDisplayInstance() {
        DisplayInstanceMap instances = new DisplayInstanceMap();
        DisplayPropertyMap shared = new DisplayPropertyMap();
        shared.put(Constants.AR_DPROP_ENABLE, new Value(1));
        instances.put(0, shared);
        DisplayPropertyMap properties = new DisplayPropertyMap();
        properties.put(Constants.AR_DPROP_LABEL, new Value("Sample label"));
        properties.put(Constants.AR_DPROP_DATA_ROWS, new Value(1L, DataType.ULONG));
        properties.put(Constants.AR_DPROP_CHARFIELD_DISPLAY_TYPE, new Value(1, DataType.ENUM));
        properties.put(Constants.AR_DPROP_MANAGE_EXPAND_BOX, new Value(1, DataType.ENUM));
        properties.put(Constants.AR_DPROP_FIELD_CUSTOMSTYLE, new Value("sample-field sample-company"));
        properties.put(Constants.AR_DPROP_ENABLE, new Value(2));
        properties.put(Constants.AR_DPROP_PROGRESSIVE_WIDTH_DESKTOP, new Value(6));
        properties.put(Constants.AR_DPROP_PROGRESSIVE_WIDTH_MOBILE, new Value(12));
        properties.put(Constants.AR_DPROP_MOBILE_VISIBLE, new Value(0));
        properties.put(Constants.AR_DPROP_BBOX, new Value(java.util.List.of(
            new CoordinateInfo(10, 20), new CoordinateInfo(200, 40)), DataType.COORDS));
        instances.put(100, properties);
        if (fieldId != 3) instances.put(101, properties);
        return instances;
    }
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
