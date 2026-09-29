package com.bmc.arsys.api;

public final class MenuCriteria {
    public static final int MENU_DEFINITION = 1 << 9;

    private int propertiesToRetrieve;

    public void setPropertiesToRetrieve(int value) {
        propertiesToRetrieve = value;
    }

    public int getPropertiesToRetrieve() {
        return propertiesToRetrieve;
    }
}
