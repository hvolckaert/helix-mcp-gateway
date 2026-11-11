package com.bmc.arsys.api;

public final class FieldCriteria {
    public static final int FIELD_NAME = 1;
    public static final int DATATYPE = 1 << 9;
    public static final int LIMIT = 1 << 14;
    public static final int DEFAULT_VALUE = 1 << 13;
    public static final int OPTION = 1 << 10;
    public static final int CREATE_MODE = 1 << 11;
    public static final int SETFIELD_OPTION = 1 << 17;
    public static final int AUDIT_OPTION = 1 << 12;
    public static final int PERMISSIONS = 1 << 1;

    private int propertiesToRetrieve;

    public void setPropertiesToRetrieve(int value) {
        propertiesToRetrieve = value;
    }

    public int getPropertiesToRetrieve() {
        return propertiesToRetrieve;
    }
}
