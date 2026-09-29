package com.bmc.arsys.api;

public final class FieldCriteria {
    public static final int FIELD_NAME = 1;
    public static final int DATATYPE = 1 << 9;
    public static final int LIMIT = 1 << 14;

    private int propertiesToRetrieve;

    public void setPropertiesToRetrieve(int value) {
        propertiesToRetrieve = value;
    }

    public int getPropertiesToRetrieve() {
        return propertiesToRetrieve;
    }
}
