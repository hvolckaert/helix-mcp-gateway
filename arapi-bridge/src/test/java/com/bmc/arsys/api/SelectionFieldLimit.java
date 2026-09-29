package com.bmc.arsys.api;

import java.util.List;

public final class SelectionFieldLimit extends FieldLimit {
    public static final int REGULAR = 1;
    public static final int CUSTOM = 2;

    private final List<EnumItem> values;

    public SelectionFieldLimit(List<EnumItem> values) {
        this.values = values;
    }

    public List<EnumItem> getValues() {
        return values;
    }

    public int getListStyle() {
        return CUSTOM;
    }
}
