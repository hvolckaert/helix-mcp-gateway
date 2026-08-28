package com.bmc.arsys.api;

public final class SortInfo {
    private final int fieldId;
    private final int order;

    public SortInfo(int fieldId, int order) {
        this.fieldId = fieldId;
        this.order = order;
    }

    public int getFieldId() {
        return fieldId;
    }

    public int getOrder() {
        return order;
    }
}
